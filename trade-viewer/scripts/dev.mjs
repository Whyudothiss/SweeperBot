import { spawn } from "node:child_process";

const environment = {
  ...process.env,
  WRANGLER_LOG_PATH: ".wrangler/wrangler.log",
};

const api = spawn("python3", ["server.py"], { stdio: "inherit", env: environment });
const site = spawn("./node_modules/.bin/vinext", ["dev"], {
  stdio: "inherit",
  env: environment,
});

function stop(code = 0) {
  api.kill("SIGTERM");
  site.kill("SIGTERM");
  process.exit(code);
}

process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
site.on("exit", (code) => stop(code ?? 0));
api.on("exit", (code) => {
  if (code !== 0) stop(code ?? 1);
});
