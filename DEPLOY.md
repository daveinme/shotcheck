# Deploy su lab.dualme.it

Presuppone: repo già clonata sul VPS, DNS di `lab.dualme.it` già puntato
sull'IP del server (record A). Se il DNS è stato aggiornato da poco,
aspetta la propagazione prima del passo certbot — verifica con:

```bash
dig +short lab.dualme.it
```

## 1. Pull e dipendenze

```bash
cd /percorso/della/repo/shotcheck   # correggi con il path reale sul server
git pull

# se il venv non esiste ancora:
python3 -m venv venv

venv/bin/pip install -r requirements.txt
```

## 2. File .env

Se non esiste ancora sul server:

```bash
cp .env.example .env
nano .env
```

Compila almeno: `SECRET_KEY` (genera con `python3 -c "import secrets; print(secrets.token_hex(32))"`),
`DATABASE_URL`, le credenziali R2, e imposta:

```
BASE_URL=https://lab.dualme.it
```

Il file `.env` non è nel repo (è in `.gitignore`) — resta solo sul server.

## 3. Migrazioni database

Se è il primo deploy su questo server, il DB SQLite si crea da solo al primo
avvio (`storage/shotcheck.db`). Se stai aggiornando un'istanza già in uso
con dati reali, controlla prima se questa sessione ha aggiunto colonne o
tabelle nuove — chiedi conferma prima di alterare lo schema in produzione.

## 4. pm2

Se pm2 non è installato sul server:

```bash
npm install -g pm2
```

Avvio (dalla cartella della repo):

```bash
pm2 start ecosystem.config.js
pm2 save
pm2 startup   # segui l'istruzione stampata per l'avvio al boot del server
```

**Prima di lanciare `pm2 start`**, apri `ecosystem.config.js` e correggi il
campo `cwd` con il path reale della repo sul server (è impostato a
`/home/deploy/shotcheck` come placeholder).

Per aggiornamenti successivi (dopo un `git pull`):

```bash
pm2 restart shotcheck
```

Log:

```bash
pm2 logs shotcheck
```

## 5. nginx

```bash
sudo apt install -y nginx   # se non è già installato

sudo cp deploy/nginx-lab.dualme.it.conf /etc/nginx/sites-available/lab.dualme.it
sudo ln -s /etc/nginx/sites-available/lab.dualme.it /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

A questo punto il sito è raggiungibile in HTTP su `http://lab.dualme.it`.

## 6. Certificato HTTPS (Let's Encrypt via certbot)

Certbot modifica automaticamente la config nginx per aggiungere il blocco
HTTPS e il redirect da HTTP, quindi va lanciato *dopo* il passo 5:

```bash
sudo apt install -y certbot python3-certbot-nginx

sudo certbot --nginx -d lab.dualme.it
```

Certbot chiede un'email (per avvisi di scadenza) e se vuoi il redirect
automatico HTTP → HTTPS (consigliato, rispondi sì). Il certificato dura 90
giorni ed è rinnovato in automatico da un timer systemd che certbot installa
da solo — verifica che sia attivo con:

```bash
sudo systemctl status certbot.timer
```

Rinnovo manuale di prova (non fa nulla se non serve ancora):

```bash
sudo certbot renew --dry-run
```

## Riepilogo comandi per un aggiornamento futuro

```bash
cd /percorso/della/repo/shotcheck
git pull
venv/bin/pip install -r requirements.txt   # solo se requirements.txt è cambiato
pm2 restart shotcheck
```

nginx e il certificato non vanno toccati per i normali aggiornamenti di
codice — solo se cambia il dominio o la porta interna.
