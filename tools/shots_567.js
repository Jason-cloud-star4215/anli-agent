/* 补拍图5/6/7：恢复页深处（y>3000）的报告区块，滚动 + 视口相对裁剪 + 2x。 */
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForDebugPort(port, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`);
      const list = await res.json();
      const page = list.find((item) => item.type === "page");
      if (page && page.webSocketDebuggerUrl) return page.webSocketDebuggerUrl;
    } catch (err) { }
    await sleep(300);
  }
  throw new Error("Chrome 调试端口未就绪");
}

function createClient(wsUrl) {
  const socket = new WebSocket(wsUrl);
  const pending = new Map();
  let nextId = 1;
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
    }
  });
  const ready = new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve);
    socket.addEventListener("error", reject);
  });
  return {
    ready,
    send: (method, params = {}) => {
      const id = nextId++;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        socket.send(JSON.stringify({ id, method, params }));
      });
    },
  };
}

const NEEDLES = [
  ["图5_金额计算说明.png", "金额计算说明"],
  ["图6_质证争点清单.png", "质证争点清单"],
  ["图7_文书草稿_民事起诉状.png", "民事起诉状"],
];

async function main() {
  const taskId = process.argv[2] || "f9312858c6b2";
  const outDir = "artifacts/shots_3x";
  fs.mkdirSync(outDir, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "anli-p567-"));
  const dbg = 9950 + Math.floor(Math.random() * 200);
  const chrome = spawn(
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
      "--hide-scrollbars", `--remote-debugging-port=${dbg}`, `--user-data-dir=${profile}`,
      "--window-size=1500,1000", "about:blank"],
    { stdio: "ignore" }
  );
  try {
    const client = createClient(await waitForDebugPort(dbg));
    await client.ready;
    await client.send("Page.enable");
    await client.send("Runtime.enable");
    await client.send("Page.addScriptToEvaluateOnNewDocument", {
      source: `try { localStorage.setItem('anli_task', ${JSON.stringify(taskId)}); } catch (e) {}`,
    });
    await client.send("Emulation.setDeviceMetricsOverride",
      { width: 1500, height: 1000, deviceScaleFactor: 2, mobile: false });
    await client.send("Page.navigate", { url: "http://127.0.0.1:8765/" });
    await sleep(6000);
    const ask = async (expr) => {
      const r = await client.send("Runtime.evaluate", { expression: expr, returnByValue: true });
      if (r.exceptionDetails) throw new Error("JS 异常: " + JSON.stringify(r.exceptionDetails).slice(0, 200));
      return r.result.value;
    };
    await ask(`document.querySelectorAll("#report details").forEach(d => { d.open = true; }); true`);
    await sleep(1000);

    for (const [file, needle] of NEEDLES) {
      const rect = await ask(`(() => {
        const within = document.getElementById('report');
        let best = null, bestLen = Infinity;
        for (const node of within.querySelectorAll('*')) {
          const own = Array.from(node.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('');
          if (own.includes(${JSON.stringify(needle)}) && node.textContent.length < bestLen) {
            best = node; bestLen = node.textContent.length;
          }
        }
        if (!best) return null;
        const el = best.closest('details') || best;
        el.scrollIntoView({ block: 'start' });
        const r = el.getBoundingClientRect();
        return { x: r.left, y: r.top, w: r.width, h: r.height };
      })()`);
      if (!rect) throw new Error("未找到: " + needle);
      console.log(needle, "视口内:", JSON.stringify(rect));
      const vh = Math.min(Math.max(Math.ceil(rect.h + 140), 1000), 5600);
      await client.send("Emulation.setDeviceMetricsOverride",
        { width: 1500, height: vh, deviceScaleFactor: 2, mobile: false });
      await sleep(1000);
      // 视口变化后重取坐标
      const rect2 = await ask(`(() => {
        const within = document.getElementById('report');
        let best = null, bestLen = Infinity;
        for (const node of within.querySelectorAll('*')) {
          const own = Array.from(node.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('');
          if (own.includes(${JSON.stringify(needle)}) && node.textContent.length < bestLen) {
            best = node; bestLen = node.textContent.length;
          }
        }
        const el = best.closest('details') || best;
        el.scrollIntoView({ block: 'start' });
        const r = el.getBoundingClientRect();
        return { x: r.left, y: r.top, w: r.width, h: r.height };
      })()`);
      await sleep(600);
      const s = await client.send("Page.captureScreenshot", {
        format: "png", clip: { x: rect2.x, y: rect2.y, width: rect2.w, height: rect2.h, scale: 1 },
      });
      fs.writeFileSync(path.join(outDir, file), Buffer.from(s.data, "base64"));
      console.log("[shot]", file, Math.round(rect2.w * 2) + "x" + Math.round(rect2.h * 2));
      await client.send("Emulation.setDeviceMetricsOverride",
        { width: 1500, height: 1000, deviceScaleFactor: 2, mobile: false });
      await sleep(500);
    }
    console.log("完成");
  } finally {
    chrome.kill();
    await sleep(300);
  }
}

main().catch((err) => { console.error("[fatal]", err.message); process.exit(1); });
