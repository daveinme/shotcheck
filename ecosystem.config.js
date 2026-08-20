module.exports = {
  apps: [
    {
      name: "shotcheck",
      // Percorso assoluto della repo sul server — CORREGGI se diverso
      cwd: "/home/deploy/shotcheck",
      script: "venv/bin/uvicorn",
      args: "app.main:app --host 127.0.0.1 --port 8000",
      interpreter: "none",
      env: {
        // uvicorn legge già .env tramite python-dotenv nel codice dell'app;
        // qui non serve duplicare le variabili.
      },
      autorestart: true,
      max_restarts: 10,
      watch: false,
    },
  ],
};
