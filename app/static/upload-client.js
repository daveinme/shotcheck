/* Lato pagina del sistema di upload: parla col service worker.
 *
 * Incluso in tutte le pagine, cosi' il pannello di avanzamento resta visibile
 * anche navigando altrove mentre i file salgono. Le pagine che caricano
 * chiamano soltanto ShotcheckUpload.accoda(...).
 */
(function () {
  const pannello = document.createElement("div");
  pannello.id = "sc-upload-panel";
  pannello.hidden = true;
  pannello.innerHTML = `
    <div class="sc-up-head">
      <span class="sc-up-title">Caricamento</span>
      <button type="button" class="sc-up-toggle" title="Riduci">–</button>
      <button type="button" class="sc-up-close" title="Chiudi" hidden>&times;</button>
    </div>
    <div class="sc-up-list"></div>`;

  // lo script sta nell'head (le pagine chiamano ShotcheckUpload subito),
  // quindi il body puo' non esistere ancora quando arriva il primo stato
  let montato = false;
  function monta() {
    if (montato) return;
    if (!document.body) {
      document.addEventListener("DOMContentLoaded", monta, { once: true });
      return;
    }
    document.body.appendChild(pannello);
    montato = true;
  }

  const titolo = () => pannello.querySelector(".sc-up-title");
  const lista = () => pannello.querySelector(".sc-up-list");

  pannello.querySelector(".sc-up-toggle").addEventListener("click", (e) => {
    const l = lista();
    const chiuso = l.style.display === "none";
    l.style.display = chiuso ? "" : "none";
    e.target.textContent = chiuso ? "–" : "+";
  });
  pannello.querySelector(".sc-up-close").addEventListener("click", () => {
    pannello.hidden = true;
    if (navigator.serviceWorker.controller)
      navigator.serviceWorker.controller.postMessage({ tipo: "pulisci" });
  });

  let inVolo = 0;   // quanti file non hanno ancora un esito

  function disegna(s) {
    if (!s.voci.length) { pannello.hidden = true; return; }
    monta();
    if (!montato) { document.addEventListener("DOMContentLoaded", () => disegna(s), { once: true }); return; }
    pannello.hidden = false;
    const fatti = s.voci.filter((v) => v.esito).length;
    const ko = s.voci.filter((v) => v.esito === "errore" || v.esito === "dubbio").length;
    inVolo = s.voci.length - fatti;
    titolo().textContent = inVolo
      ? `Caricamento ${fatti}/${s.voci.length}`
      : ko ? `Completato: ${fatti - ko} ok, ${ko} da controllare`
           : `Completato: ${fatti} file`;
    pannello.querySelector(".sc-up-close").hidden = inVolo > 0;
    lista().innerHTML = s.voci.map((v) => {
      const stato = v.esito === "ok" ? "✓"
                  : v.esito === "errore" ? "Errore"
                  : v.esito === "dubbio" ? "Da verificare"
                  : v.pct + "%";
      const cls = v.esito === "ok" ? " sc-up-ok"
                : (v.esito === "errore" || v.esito === "dubbio") ? " sc-up-ko" : "";
      return `<div class="sc-up-row${cls}"><span class="sc-up-name">${v.nome}</span>` +
             `<span class="sc-up-pct">${stato}</span></div>`;
    }).join("");
  }

  // Chiudere il tab interrompe davvero gli upload: il browser mostra la sua
  // richiesta di conferma solo se c'e' qualcosa in volo.
  window.addEventListener("beforeunload", (e) => {
    if (inVolo > 0) { e.preventDefault(); e.returnValue = ""; return ""; }
  });

  // Se il browser termina il worker a meta' coda, la coda su IndexedDB resta
  // ma nessuno la fa ripartire: questo battito lo risveglia finche' serve.
  setInterval(() => {
    if (inVolo > 0 && navigator.serviceWorker.controller)
      navigator.serviceWorker.controller.postMessage({ tipo: "chiedi-stato" });
  }, 10000);

  const ascoltatori = [];

  navigator.serviceWorker.addEventListener("message", (e) => {
    const m = e.data || {};
    if (m.tipo === "stato") disegna(m);
    else if (m.tipo === "finito") ascoltatori.forEach((fn) => fn(m));
  });

  const pronto = navigator.serviceWorker.register("/upload-worker.js", { scope: "/" })
    .then(() => navigator.serviceWorker.ready)
    .then((reg) => {
      // chiede subito lo stato: se arrivo da un'altra pagina, gli upload
      // partiti li' sono ancora in corso e vanno ridisegnati qui
      const sw = reg.active || navigator.serviceWorker.controller;
      if (sw) sw.postMessage({ tipo: "chiedi-stato" });
      return reg;
    });

  window.ShotcheckUpload = {
    async accoda(files, { url, folderId, batchKey }) {
      const reg = await pronto;
      const sw = reg.active || navigator.serviceWorker.controller;
      if (!sw) return;
      sw.postMessage({ tipo: "accoda", files, url, folderId, batchKey });
    },
    // la pagina si registra per sapere quando un suo file e' arrivato
    alTermine(fn) { ascoltatori.push(fn); },
  };
})();
