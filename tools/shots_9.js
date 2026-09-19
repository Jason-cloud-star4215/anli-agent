/* 补拍图9：8 万任务恢复页，滚动到目标区块再截（深目标用滚动+局部视口）。 */
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

async function main() {
  const taskId = process.argv[2] || "ff3e35b3e945";
  const outDir = "artifacts/shots_3x";
  fs.mkdirSync(outDir, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "anli-p9b-"));
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
      { width: 1500, height: 1000, deviceScaleFactor: 3, mobile: false });
    await client.send("Page.navigate", { url: "http://127.0.0.1:8765/" });
    await sleep(6000);
    const ask = async (expr) => {
      const r = await client.send("Runtime.evaluate", { expression: expr, returnByValue: true });
      return r.result.value;
    };
    await ask(`document.querySelectorAll("#report details").forEach(d => { d.open = true; }); true`);
    const rect = await ask(`(() => {
      const needle = '样本与本案损失量级不匹配';
      let best = null, bestLen = Infinity;
      for (const node of document.querySelectorAll('#report *')) {
        const own = Array.from(node.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('');
        if (own.includes(needle) && node.textContent.length < bestLen) { best = node; bestLen = node.textContent.length; }
      }
      if (!best) return null;
      let node = best, target = null;
      while (node && !node.matches('#report')) {
        const r = node.getBoundingClientRect();
        if (r.height > 200) { target = node; break; }
        node = node.parentElement;
      }
      const el = target || best.closest('details') || best;
      const r = el.getBoundingClientRect();
      return { x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height };
    })()`);
    if (!rect) throw new Error("未找到量级闸门区块");
    console.log("目标块:", JSON.stringify(rect));
    // 目标在页面深处（y≈5000）：3x 下会撞 GPU 纹理上限（16384/3≈5461），改用 2x + 高视口一次覆盖
    await client.send("Emulation.setDeviceMetricsOverride",
      { width: 1500, height: Math.min(Math.ceil(rect.y + rect.h + 100), 5600), deviceScaleFactor: 2, mobile: false });
    await sleep(1500);
    const s = await client.send("Page.captureScreenshot", {
      format: "png", clip: { x: rect.x, y: rect.y, width: rect.w, height: rect.h, scale: 1 },
    });
    const file = path.join(outDir, "图9_量级闸门_暂不提供.png");
    fs.writeFileSync(file, Buffer.from(s.data, "base64"));
    console.log("[shot] 图9 ->", file, Math.round(rect.w * 2) + "x" + Math.round(rect.h * 2));
    await ask(`window.scrollTo(0, 0); true`);
  } finally {
    chrome.kill();
    await sleep(300);
  }
}

main().catch((err) => { console.error("[fatal]", err.message); process.exit(1); });
