import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { createAuth } from "./auth";
import { loadConfig } from "./config";
import "./styles.css";

const root = createRoot(document.getElementById("root")!);
loadConfig().then(
  (cfg) => root.render(<StrictMode><App cfg={cfg} auth={createAuth(cfg)} /></StrictMode>),
  (e) => root.render(<p role="alert">This app is not configured yet: {String(e.message)}</p>),
);
