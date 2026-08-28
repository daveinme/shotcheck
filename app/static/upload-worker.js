/* Service Worker: possiede la coda di upload.
 *
 * Vive fuori dalle pagine, quindi passare da /admin/batch/3 a /admin non
 * interrompe piu' i caricamenti in corso: le pagine si limitano a chiedergli
 * lo stato e a ridisegnarlo.
 *
 * La coda resta SERIALE anche fra batch diversi: SQLite non tollera due
 * INSERT concorrenti ("database is locked"), ed e' il motivo per cui il
 * codice che questo sostituisce accodava invece di parallelizzare.
 */
const coda = [];              // { id, file, url, folderId, batchKey, nome }
const stato = new Map();      // id -> { nome, pct, esito, batchKey }
let inCorso = false;
let contatore = 0;

async function avvisaPagine(msg) {
  const clients = await self.clients.matchAll({ includeUncontrolled: true, type: "window" });
  for (const c of clients) c.postMessage(msg);
}

function istantanea() {
  const voci = [...stato.entries()].map(([id, v]) => ({ id, ...v }));
  return {
    tipo: "stato",
    voci,
    inCoda: coda.length,
    attivo: inCorso,
  };
}

// XMLHttpRequest NON esiste nei service worker: si usa fetch. In cambio
// fetch non espone il progresso di upload, quindi il pannello segna il file
// come "in corso" e passa a 100% quando il server risponde. Con un file per
// volta resta chiaro a che punto siamo.
async function caricaUno(job) {
  const fd = new FormData();
  fd.append("files", job.file);
  if (job.folderId) fd.append("folder_id", job.folderId);

  const v = stato.get(job.id);
  if (v) { v.pct = 1; avvisaPagine(istantanea()); }

  let ok = false, uploads = [];
  try {
    const risp = await fetch(job.url, { method: "POST", body: fd, credentials: "same-origin" });
    ok = risp.ok;
    if (ok) {
      try { uploads = (await risp.json()).uploads || []; } catch (e) { /* risposta non JSON */ }
    }
  } catch (e) {
    ok = false;
  }

  if (v) { v.esito = ok ? "ok" : "errore"; v.pct = 100; }
  avvisaPagine({ tipo: "finito", id: job.id, ok, uploads,
                 batchKey: job.batchKey, folderId: job.folderId });
}

async function svuota() {
  if (inCorso) return;
  inCorso = true;
  while (coda.length) {
    const job = coda.shift();
    await caricaUno(job);
  }
  inCorso = false;
  avvisaPagine(istantanea());
}

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("message", (e) => {
  const m = e.data || {};
  if (m.tipo === "accoda") {
    for (const file of m.files) {
      const id = `u${++contatore}`;
      stato.set(id, { nome: file.name, pct: 0, esito: null, batchKey: m.batchKey });
      coda.push({ id, file, url: m.url, folderId: m.folderId,
                  batchKey: m.batchKey, nome: file.name });
    }
    avvisaPagine(istantanea());
    svuota();
  } else if (m.tipo === "chiedi-stato") {
    e.source && e.source.postMessage(istantanea());
  } else if (m.tipo === "pulisci") {
    for (const [id, v] of [...stato.entries()]) if (v.esito) stato.delete(id);
    avvisaPagine(istantanea());
  }
});
