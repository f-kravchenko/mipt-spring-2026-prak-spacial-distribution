import React from "react";
import ReactDOM from "react-dom/client";
// Шрифты бандлятся локально (@fontsource) — без CDN, работает офлайн.
// Golos Text — гротеск с родной кириллицей (UI), IBM Plex Mono — табличные цифры.
import "@fontsource/golos-text/400.css";
import "@fontsource/golos-text/500.css";
import "@fontsource/golos-text/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles.css";
import App from "./App.jsx";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
