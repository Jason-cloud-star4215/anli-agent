/* 重拍图13 两段：实拍日志（滚动到「模型决策」行）+ 恢复页的§六检索决策留痕（2x 深视口）。 */
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
  let client;
  const outDir = "artifacts/shots_3x";
  fs.mkdirSync(outDir, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "anli-p13-"));
  const dbg = 9950 + Math.floor(Math.random() * 200);
  const chrome = spawn(
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
      "--hide-scrollbars", `--remote-debugging-port=${dbg}`, `--user-data-dir=${profile}`,
      "--window-size=1500,1000", "about:blank"],
    { stdio: "ignore" }
  );
  const evalJs = async (expression) => {
    const r = await client.send("Runtime.evaluate", { expression, returnByValue: true });
    if (r.exceptionDetails) throw new Error("JS 异常: " + JSON.stringify(r.exceptionDetails).slice(0, 300));
    return r.result.value;
  };
  const save = async (file, clip) => {
    const s = await client.send("Page.captureScreenshot", { format: "png", clip });
    fs.writeFileSync(path.join(outDir, file), Buffer.from(s.data, "base64"));
    console.log("[shot]", file, Math.round(clip.width) + "x" + Math.round(clip.height));
  };
  try {
    client = createClient(await waitForDebugPort(dbg));
    await client.ready;
    await client.send("Page.enable");
    await client.send("Runtime.enable");
    await client.send("Emulation.setDeviceMetricsOverride",
      { width: 1500, height: 1000, deviceScaleFactor: 3, mobile: false });
    await client.send("Page.navigate", { url: "http://127.0.0.1:8765/?demo=car" });

    const deadline = Date.now() + 300000;
    while (Date.now() < deadline) {
      const ok = await evalJs(
        `document.getElementById('runLabel').textContent.trim() !== '分析中…'
         && document.getElementById('report').textContent.length > 800`);
      if (ok) break;
      await sleep(2000);
    }
    // 等动画稳定
    const read = `(() => [document.getElementById('log')?.textContent.length || 0,
      document.getElementById('report')?.textContent.length || 0].join(','))()`;
    let last = await evalJs(read), stable = 1;
    const d2 = Date.now() + 120000;
    while (Date.now() < d2) {
      await sleep(2000);
      const cur = await evalJs(read);
      if (cur === last) { stable += 1; if (stable >= 3) break; } else { stable = 1; last = cur; }
    }

    // 上半：日志滚到第一条「模型决策」行
    const dec = await evalJs(`(() => {
      const log = document.getElementById('log');
      const rows = Array.from(log.querySelectorAll('*')).filter(el =>
        el.children.length === 0 && /模型决策|规则决策/.test(el.textContent));
      if (rows.length === 0) return { count: 0 };
      const target = rows[0];
      target.scrollIntoView({ block: 'start' });
      log.scrollTop -= 60;
      return { count: rows.length, first: target.textContent.slice(0, 60) };
    })()`);
    console.log("决策行:", JSON.stringify(dec));
    await sleep(800);
    await save("图13_决策行.png", await evalJs(`(() => {
      const el = document.getElementById('log').closest('.card');
      const r = el.getBoundingClientRect();
      return { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height, scale: 1 };
    })()`));

    // 下半：恢复页的§六检索决策留痕
    const saved = await evalJs(`localStorage.getItem('anli_task') || ''`);
    await client.send("Page.navigate", { url: "http://127.0.0.1:8765/" });
    await sleep(1500);
    await evalJs(`document.querySelectorAll("#report details").forEach(d => { d.open = true; }); true`);
    await sleep(1500);
    const trace = await evalJs(`(() => {
      const sections = Array.from(document.querySelectorAll('#report details'));
      const sec = sections.find(d => d.querySelector('summary') && d.querySelector('summary').textContent.includes('法条与类案'));
      if (!sec) return null;
      const pre = sec.querySelector('pre') || sec;
      const r = pre.getBoundingClientRect();
      const tailH = Math.min(r.height, 460);
      return { x: r.left + scrollX, y: r.top + scrollY + r.height - tailH, w: r.width, h: tailH, pageH: document.documentElement.scrollHeight };
    })()`);
    if (!trace) throw new Error("未找到§六留痕");
    console.log("留痕块:", JSON.stringify(trace));
    // 深目标正确姿势：滚动把目标带进视口 → 视口加高 → 用「视口相对坐标」截取
    await client.send("Emulation.setDeviceMetricsOverride",
      { width: 1500, height: Math.max(1000, Math.ceil(trace.h + 120)), deviceScaleFactor: 2, mobile: false });
    await sleep(800);
    const vp = await evalJs(`(() => {
      const sections = Array.from(document.querySelectorAll('#report details'));
      const sec = sections.find(d => d.querySelector('summary') && d.querySelector('summary').textContent.includes('法条与类案'));
      const pre = sec.querySelector('pre') || sec;
      pre.scrollIntoView({ block: 'end' });
      const r = pre.getBoundingClientRect();
      const tailH = Math.min(r.height, 460);
      return { x: r.left, y: r.top + r.height - tailH, w: r.width, h: tailH };
    })()`);
    console.log("视口内块:", JSON.stringify(vp));
    await save("图13_留痕.png", { x: vp.x, y: vp.y, width: vp.w, height: vp.h, scale: 1 });
    console.log("完成。决策行来源任务:", saved);
  } finally {
    chrome.kill();
    await sleep(300);
  }
}

main().catch((err) => { console.error("[fatal]", err.message); process.exit(1); });
