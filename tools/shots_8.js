/* 图8 重拍：headless Chrome 直接渲染验收证据 txt 开头（不用记事本，避免截到无关窗口）。 */
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "anli-p8-"));
  const dbg = 9970 + Math.floor(Math.random() * 100);
  const chrome = spawn(
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    ["--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
      `--remote-debugging-port=${dbg}`, `--user-data-dir=${profile}`,
      "--window-size=1500,1080", "about:blank"],
    { stdio: "ignore" }
  );
  try {
    let wsUrl = null;
    for (let i = 0; i < 40 && !wsUrl; i += 1) {
      try {
        const res = await fetch(`http://127.0.0.1:${dbg}/json/list`);
        const list = await res.json();
        const page = list.find((item) => item.type === "page");
        if (page) wsUrl = page.webSocketDebuggerUrl;
      } catch (e) {}
      if (!wsUrl) await sleep(300);
    }
    if (!wsUrl) throw new Error("Chrome 调试端口未就绪");
    const socket = new WebSocket(wsUrl);
    await new Promise((r, j) => { socket.addEventListener("open", r); socket.addEventListener("error", j); });
    let mid = 0;
    const pending = new Map();
    socket.addEventListener("message", (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) { const res = pending.get(m.id); pending.delete(m.id); res(m.result); }
    });
    const send = (method, params = {}) => new Promise((res) => {
      mid += 1;
      pending.set(mid, res);
      socket.send(JSON.stringify({ id: mid, method, params }));
    });
    await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride",
      { width: 1500, height: 1080, deviceScaleFactor: 2, mobile: false });
    const txt = path.resolve("artifacts/验收证据.txt").replace(/\\/g, "/");
    await send("Page.navigate", { url: "file:///" + txt });
    await sleep(2500);
    const s = await send("Page.captureScreenshot",
      { format: "png", clip: { x: 0, y: 0, width: 1500, height: 1080, scale: 1 } });
    const out = path.resolve("artifacts/shots_3x/图8_验收证据文件.png");
    fs.writeFileSync(out, Buffer.from(s.data, "base64"));
    console.log("[shot] 图8 ->", out);
  } finally {
    chrome.kill();
    await sleep(300);
  }
})();
