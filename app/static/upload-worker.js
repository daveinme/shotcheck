/* Service Worker: possiede la coda di upload.
 *
 * Vive fuori dalle pagine, quindi passare da /admin/batch/3 a /admin non
 * interrompe i caricamenti in corso: le pagine chiedono lo stato e lo
 * ridisegnano.
 *
 * DUE VINCOLI che hanno gia' rotto una versione precedente:
 *
 * 1. Il browser TERMINA un service worker che sembra inattivo (~30s). Ogni
 *    gestore di eventi deve percio' tenere vivo il worker con waitUntil()
 *    finche' la coda non e' finita, altrimenti su code lunghe (376 file) il
 *    caricamento si ferma a meta' senza errori nei log del server.
 * 2. Se nonostante tutto viene terminato, la memoria sparisce: la coda va
 *    persistita su IndexedDB, non tenuta solo in un array. I File/Blob
 *    sopravvivono in IndexedDB, quindi al risveglio la coda riparte da sola.
 *
 * La coda resta SERIALE anche fra batch diversi: SQLite non tollera due
 * INSERT concorrenti ("database is locked").
 */
const DB_NOME = "shotcheck-upload";
const STORE = "coda";

function apriDb() {
  return new Promise((res, rej) => {
    const r = indexedDB.open(DB_NOME, 1);
    r.onupgradeneeded = () => {
      if (!r.result.objectStoreNames.contains(STORE))
        r.result.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
    };
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}

function tx(db, modo, fn) {
  return new Promise((res, rej) => {
    const t = db.transaction(STORE, modo);
    const richiesta = fn(t.objectStore(STORE));
    t.oncomplete = () => res(richiesta && richiesta.result);
    t.onerror = () => rej(t.error);
  });
}

async function aggiungiJob(job) {
  const db = await apriDb();
  return tx(db, "readwrite", (s) => s.add(job));
}

async function leggiTutti() {
  const db = await apriDb();
  return tx(db, "readonly", (s) => s.getAll());
}

async function aggiornaJob(job) {
  const db = await apriDb();
  return tx(db, "readwrite", (s) => s.put(job));
}

async function eliminaConclusi() {
  const db = await apriDb();
  const tutti = await leggiTutti();
  for (const j of tutti) if (j.esito) await tx(db, "readwrite", (s) => s.delete(j.id));
}

let inCorso = false;
// permette di abortire il file attualmente in volo: senza, un annullamento
// lascerebbe comunque arrivare al server l'ultimo POST gia' partito
let inVoloController = null;

async function avvisaPagine(msg) {
  const clients = await self.clients.matchAll({ includeUncontrolled: true, type: "window" });
  for (const c of clients) c.postMessage(msg);
}

async function istantanea() {
  const tutti = await leggiTutti();
  return {
    tipo: "stato",
    voci: tutti.map((j) => ({ id: j.id, nome: j.nome, pct: j.pct || 0,
                              esito: j.esito || null, batchKey: j.batchKey })),
    inCoda: tutti.filter((j) => !j.esito).length,
    attivo: inCorso,
  };
}

// XMLHttpRequest NON esiste nei service worker: si usa fetch. In cambio fetch
// non espone il progresso di upload, quindi il file resta "in corso" e passa
// a 100% quando il server risponde.
async function caricaUno(job) {
  const fd = new FormData();
  fd.append("files", job.file);
  if (job.folderId) fd.append("folder_id", job.folderId);

  // marcato PRIMA del fetch: se il worker viene terminato a meta' richiesta,
  // al risveglio questo job non viene ritentato alla cieca (il server
  // potrebbe averlo gia' salvato -> foto duplicata). Finisce fra i dubbi.
  job.pct = 1;
  job.tentato = true;
  await aggiornaJob(job);
  await avvisaPagine(await istantanea());

  let ok = false, uploads = [], annullato = false;
  inVoloController = new AbortController();
  try {
    const risp = await fetch(job.url, { method: "POST", body: fd,
                                        credentials: "same-origin",
                                        signal: inVoloController.signal });
    ok = risp.ok;
    if (ok) {
      try { uploads = (await risp.json()).uploads || []; } catch (e) { /* risposta non JSON */ }
    }
  } catch (e) {
    ok = false;
    annullato = e && e.name === "AbortError";
  } finally {
    inVoloController = null;
  }

  // il file caricato non serve piu': tenerlo in IndexedDB sprecherebbe
  // spazio per tutta la sessione (376 foto sono parecchi GB)
  job.esito = ok ? "ok" : annullato ? "annullato" : "errore";
  job.pct = 100;
  job.file = null;
  await aggiornaJob(job);

  await avvisaPagine({ tipo: "finito", id: job.id, ok, uploads,
                       batchKey: job.batchKey, folderId: job.folderId });
}

let annullaRichiesto = false;

async function svuota() {
  if (inCorso) return;
  inCorso = true;
  annullaRichiesto = false;
  try {
    for (;;) {
      const tutti = await leggiTutti();
      if (annullaRichiesto) break;
      const prossimo = tutti.find((j) => !j.esito && j.file && !j.tentato);
      if (!prossimo) break;
      await caricaUno(prossimo);
    }
    // job interrotti a meta' richiesta: non li ritento da solo, li segnalo
    const sospesi = (await leggiTutti()).filter((j) => !j.esito && j.tentato);
    for (const j of sospesi) {
      j.esito = "dubbio";
      j.file = null;
      await aggiornaJob(j);
    }
  } finally {
    inCorso = false;
    await avvisaPagine(await istantanea());
  }
}

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  await self.clients.claim();
  // se il worker era stato terminato con la coda a meta', riprende da solo
  await svuota();
})()));

self.addEventListener("message", (e) => {
  const m = e.data || {};
  // waitUntil: senza, il browser considera il worker inattivo e lo termina
  // mentre la coda sta ancora caricando
  e.waitUntil((async () => {
    if (m.tipo === "accoda") {
      for (const file of m.files) {
        await aggiungiJob({ nome: file.name, file, url: m.url,
                            folderId: m.folderId ?? null, batchKey: m.batchKey,
                            pct: 0, esito: null });
      }
      await avvisaPagine(await istantanea());
      await svuota();
    } else if (m.tipo === "chiedi-stato") {
      const s = await istantanea();
      if (e.source) e.source.postMessage(s); else await avvisaPagine(s);
      // se c'e' lavoro arretrato (worker risvegliato), riprendilo
      await svuota();
    } else if (m.tipo === "annulla") {
      // ferma la coda, aborta il file in volo e scarta quelli non partiti.
      // I file gia' caricati restano sul server: vanno eliminati dalla pagina.
      annullaRichiesto = true;
      if (inVoloController) inVoloController.abort();
      const tutti = await leggiTutti();
      for (const j of tutti) {
        if (!j.esito) { j.esito = "annullato"; j.file = null; await aggiornaJob(j); }
      }
      await avvisaPagine(await istantanea());
    } else if (m.tipo === "pulisci") {
      await eliminaConclusi();
      await avvisaPagine(await istantanea());
    }
  })());
});
