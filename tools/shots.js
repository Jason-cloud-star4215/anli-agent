/*
 * 高分辨率重拍提交包截图（3x 像素密度）。
 *
 * 两阶段拍摄，规避两类坑：
 *   ① 实拍阶段只拍「过程日志」（图2/图13上半）——日志是 SSE 现场数据，
 *      检查点不保存；先滚到角色交锋段落再拍。
 *   ② 重载恢复阶段拍其余全部——LIVE 页面的 reveal 动画在 headless 下
 *      会停在初始隐藏态（画面空白但 DOM 完整），刷新走检查点恢复后
 *      渲染一次到位（已验证）。
 *
 * 用法：先起服务（python app.py），然后
 *   node tools/shots.js --out artifacts/shots_3x
 * 图8/图11 不重拍（原图分辨率足够）。
 */

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

function parseArgs(argv) {
  const args = {
    url: "http://127.0.0.1:8765/?demo=car",
    out: "artifacts/shots_3x",
    width: 1500,
    height: 1000,
    scale: 3,
    chrome: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  };
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i].replace(/^--/, "");
    const value = argv[i + 1];
    if (key in args && value !== undefined) {
      args[key] = /^\d+$/.test(value) ? Number(value) : value;
      i += 1;
    }
  }
  return args;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForDebugPort(port, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`);
      const list = await res.json();
      const page = list.find((item) => item.type === "page");
      if (page && page.webSocketDebuggerUrl) return page.webSocketDebuggerUrl;
    } catch (err) { /* 端口还没起来 */ }
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
  function send(method, params = {}) {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }
  return { ready, send, close: () => socket.close() };
}

async function main() {
  const args = parseArgs(process.argv);
  fs.mkdirSync(args.out, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "anli-shot-"));
  const dbg = 9300 + Math.floor(Math.random() * 300);
  const chrome = spawn(
    args.chrome,
    [
      "--headless=new", "--disable-gpu", "--no-first-run",
      "--no-default-browser-check", "--hide-scrollbars",
      `--remote-debugging-port=${dbg}`, `--user-data-dir=${profile}`,
      `--window-size=${args.width},${args.height}`, "about:blank",
    ],
    { stdio: "ignore", detached: false }
  );

  const evalJs = async (expression) => {
    const r = await client.send("Runtime.evaluate", { expression, returnByValue: true });
    if (r.exceptionDetails) throw new Error("页面 JS 异常: " + JSON.stringify(r.exceptionDetails).slice(0, 300));
    return r.result.value;
  };

  const clipOf = async (locator) => {
    const rect = await evalJs(`(() => {
      let el = null;
      ${locator.selector ? `el = document.querySelector(${JSON.stringify(locator.selector)});` : ""}
      ${locator.text ? `
        const needle = ${JSON.stringify(locator.text)};
        const within = document.querySelector(${JSON.stringify(locator.within || "body")});
        let best = null, bestLen = Infinity;
        for (const node of within.querySelectorAll("*")) {
          const own = Array.from(node.childNodes).filter(n => n.nodeType === 3)
            .map(n => n.textContent).join("");
          if (own.includes(needle) && node.textContent.length < bestLen) {
            best = node; bestLen = node.textContent.length;
          }
        }
        el = best;
        if (el) {
          if (el.closest("#report") && !el.matches("details")) el = el.closest("details") || el;
          else if (!el.closest("#report")) {
            let node = el, target = null;
            while (node && node !== document.body) {
              const r = node.getBoundingClientRect();
              if (r.height > 200) { target = node; break; }
              node = node.parentElement;
            }
            if (target) el = target;
          }
        }` : ""}
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height };
    })()`);
    if (!rect || rect.w < 5 || rect.h < 5) throw new Error("未找到目标元素: " + JSON.stringify(locator).slice(0, 120));
    return { x: rect.x, y: rect.y, width: rect.w, height: rect.h, scale: 1 };
  };

  const shot = async (name, clip) => {
    // headless 只绘制视口高度内的内容：先把视口加高到能容下目标块，再视口内截取
    const needed = Math.min(Math.ceil(clip.y + clip.height + 60), 3000);
    if (needed > args.height) {
      await client.send("Emulation.setDeviceMetricsOverride", {
        width: args.width, height: needed, deviceScaleFactor: args.scale, mobile: false,
      });
      await sleep(900);
    }
    const r = await client.send("Page.captureScreenshot", {
      format: "png", clip,
    });
    if (needed > args.height) {
      await client.send("Emulation.setDeviceMetricsOverride", {
        width: args.width, height: args.height, deviceScaleFactor: args.scale, mobile: false,
      });
      await sleep(400);
    }
    const file = path.join(args.out, name);
    fs.writeFileSync(file, Buffer.from(r.data, "base64"));
    console.log(`[shot] ${name}  ${Math.round(clip.width * args.scale)}x${Math.round(clip.height * args.scale)}`);
  };

  const waitFor = async (expression, timeoutMs, label) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (await evalJs(expression)) return;
      await sleep(2000);
    }
    throw new Error("等待超时: " + label);
  };

  // 打字机/滑入动画播完的判据：关键面板文本长度连续 3 次轮询（间隔 2 秒）不变
  const waitStable = async (timeoutMs = 180000) => {
    const read = `(() => [document.getElementById('summaryText')?.textContent.length || 0,
      document.getElementById('statRow')?.children.length || 0,
      document.getElementById('citations')?.textContent.length || 0,
      document.getElementById('log')?.textContent.length || 0,
      document.getElementById('report')?.textContent.length || 0].join(','))()`;
    const deadline = Date.now() + timeoutMs;
    let last = await evalJs(read);
    let stable = 1;
    while (Date.now() < deadline) {
      await sleep(2000);
      const cur = await evalJs(read);
      if (cur === last) {
        stable += 1;
        if (stable >= 3) return;
      } else {
        stable = 1;
        last = cur;
      }
    }
    console.log("[warn] 等待动画稳定超时，按当前画面继续");
  };

  const reloadAndRestore = async (timeoutMs = 60000) => {
    await client.send("Page.navigate", { url: "http://127.0.0.1:8765/" });
    await waitFor(
      `document.getElementById('restoreHint') && !document.getElementById('restoreHint').classList.contains('d-none')
       && document.getElementById('report').textContent.length > 500`,
      timeoutMs, "检查点恢复");
    await sleep(2000);
    await evalJs(`document.querySelectorAll("#report details").forEach(d => { d.open = true; }); true`);
    await sleep(1500);
  };

  try {
    const wsUrl = await waitForDebugPort(dbg);
    client = createClient(wsUrl);
    await client.ready;
    await client.send("Page.enable");
    await client.send("Runtime.enable");
    await client.send("Emulation.setDeviceMetricsOverride", {
      width: args.width, height: args.height, deviceScaleFactor: args.scale, mobile: false,
    });
    await client.send("Page.navigate", { url: args.url });

    // ---------- 任务 A 现场阶段：只拍过程日志（图2/图13上半） ----------
    await waitFor(
      `document.getElementById('runLabel') && document.getElementById('runLabel').textContent.trim() !== '分析中…'
       && document.querySelectorAll('#report details').length >= 5
       && document.getElementById('report').textContent.length > 800`,
      300000, "demo=car 完成");
    await waitStable();

    await evalJs(`(() => {
      const log = document.getElementById('log');
      log.scrollTop = log.scrollHeight * 0.42;
      return log.scrollTop;
    })()`);
    await sleep(800);
    await shot("图2_过程日志.png", await clipOf({ selector: "#log" }));

    // ---------- 任务 A 恢复阶段：其余全部 ----------
    await reloadAndRestore();
    await shot("图1_整体三栏界面.png", { x: 0, y: 0, width: args.width, height: args.height, scale: 1 });
    await shot("图3_引用核验.png", await clipOf({ selector: "#citations" }));
    await shot("图4_风险与漏洞.png", await clipOf({ selector: "#findings" }));
    for (const [file, needle] of [
      ["图5_金额计算说明.png", "金额计算说明"],
      ["图6_质证争点清单.png", "质证争点清单"],
      ["图7_文书草稿_民事起诉状.png", "民事起诉状"],
    ]) {
      await shot(file, await clipOf({ text: needle, within: "#report" }));
    }
    await shot("图14_备用_结论摘要与统计卡.png", await clipOf({ selector: "#summaryCard" }));
    const stats = await evalJs(`(() => {
      const text = document.getElementById('summaryCard').innerText.replace(/\\n/g, ' ');
      const calls = text.match(/(\\d+)\\s*次/) || [];
      const secs = text.match(/([\\d.]+)\\s*秒/) || [];
      return { calls: calls[1] || '?', seconds: secs[1] || '?' };
    })()`);
    console.log("[stats] 图14 那次运行：", JSON.stringify(stats));
    const trace = await evalJs(`(() => {
      const sections = Array.from(document.querySelectorAll('#report details'));
      const sec = sections.find(d => d.querySelector('summary') && d.querySelector('summary').textContent.includes('法条与类案'));
      if (!sec) return null;
      const pre = sec.querySelector('pre') || sec;
      const r = pre.getBoundingClientRect();
      const tailH = Math.min(r.height, 460);
      return { x: r.left + window.scrollX, y: r.top + window.scrollY + r.height - tailH, w: r.width, h: tailH };
    })()`);
    if (trace) {
      await shot("图13_留痕.png", { x: trace.x, y: trace.y, width: trace.w, height: trace.h, scale: 1 });
    } else {
      console.log("[warn] 未找到§九，图13 下半改用整份报告");
      await shot("图13_留痕.png", await clipOf({ selector: "#report" }));
    }

    // ---------- 任务 B：8 万医疗费案（量级闸门 + 转人工），恢复阶段拍摄 ----------
    await client.send("Page.navigate", { url: "http://127.0.0.1:8765/" });
    await sleep(1500);
    await evalJs(`(() => {
      const desc = document.getElementById('desc');
      desc.value = '6月10号我父亲骑电动车被小轿车撞了，对方主要责任，住院治疗花了医疗费8万元，我要求对方一共赔90万元。';
      desc.dispatchEvent(new Event('input', { bubbles: true }));
      document.getElementById('run').click();
      return true;
    })()`);
    await waitFor(
      `document.getElementById('runLabel') && document.getElementById('runLabel').textContent.trim() !== '分析中…'
       && document.getElementById('report').textContent.includes('量级不匹配')`,
      300000, "8万案完成");
    await waitStable();
    await reloadAndRestore();
    await shot("图9_量级闸门_暂不提供.png",
      await clipOf({ text: "样本与本案损失量级不匹配", within: "#report" }));
    await shot("图10_转人工闸门.png", await clipOf({ selector: "#humanCard" }));

    console.log("[done] 全部截图完成 ->", path.resolve(args.out));
  } finally {
    chrome.kill();
    await sleep(300);
  }
}

let client;
main().catch((err) => { console.error("[fatal]", err.message); process.exit(1); });
