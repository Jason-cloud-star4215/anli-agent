/*
 * 用 Chrome DevTools Protocol 录一段真实演示画面，再用 ffmpeg 编码成 mp4。
 *
 * 为什么不用固定截图拼：过程流是「按节奏铺开 + 打字机」的动画，
 * 只有录真实帧才能看出「系统在做什么」；虚拟时间截图会丢掉这个观感。
 *
 * 用法（需要 Node 22+，Node 内置 WebSocket）：
 *   node tools/record_demo.js --url "http://127.0.0.1:8765/?demo=car" --out artifacts/demo
 *
 * 产出：
 *   <out>.mp4          成片（默认压到 30 秒左右）
 *   <out>-frames/*.png 原始帧与时间戳（便于重新剪辑）
 */

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

function parseArgs(argv) {
  const args = {
    url: "http://127.0.0.1:8765/?demo=car",
    out: "artifacts/demo",
    width: 1600,
    height: 900,
    target: 30,
    maxSeconds: 150,
    tailSeconds: 6,
    hold: 6,
    frames: "",
    description: "",
    chrome: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    ffmpeg: process.env.ANLI_FFMPEG || "ffmpeg",
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
    } catch (err) {
      /* 端口还没起来，继续等 */
    }
    await sleep(300);
  }
  throw new Error("Chrome 调试端口未就绪");
}

function createClient(wsUrl) {
  const socket = new WebSocket(wsUrl);
  const pending = new Map();
  const listeners = new Map();
  let nextId = 1;

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
      return;
    }
    const handlers = listeners.get(message.method) || [];
    handlers.forEach((handler) => handler(message.params));
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

  function on(method, handler) {
    if (!listeners.has(method)) listeners.set(method, []);
    listeners.get(method).push(handler);
  }

  return { ready, send, on, close: () => socket.close() };
}

async function captureFrames(args) {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "anli-rec-"));
  const port = 9222 + Math.floor(Math.random() * 200);
  const chrome = spawn(
    args.chrome,
    [
      "--headless=new",
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      "--hide-scrollbars",
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${profile}`,
      `--window-size=${args.width},${args.height}`,
      "about:blank",
    ],
    { stdio: "ignore", detached: false }
  );

  const framesDir = `${args.out}-frames`;
  fs.mkdirSync(framesDir, { recursive: true });

  try {
    const wsUrl = await waitForDebugPort(port);
    const client = createClient(wsUrl);
    await client.ready;
    await client.send("Page.enable");
    await client.send("Runtime.enable");

    const frames = [];
    let index = 0;
    client.on("Page.screencastFrame", (params) => {
      const file = path.join(framesDir, `frame-${String(index).padStart(5, "0")}.png`);
      fs.writeFileSync(file, Buffer.from(params.data, "base64"));
      frames.push({
        file: path.basename(file),
        t: Number(params.metadata.timestamp) || Date.now() / 1000,
        width: params.metadata.deviceWidth || args.width,
        height: params.metadata.deviceHeight || args.height,
      });
      index += 1;
      client.send("Page.screencastFrameAck", { sessionId: params.sessionId }).catch(() => {});
    });

    await client.send("Page.navigate", { url: args.url });
    await sleep(1200);
    // 传 --description 时：不用 ?demo 预填，直接把自定义案情填进表单再开录
    if (args.description) {
      await client
        .send("Runtime.evaluate", {
          expression:
            "(() => { const d = document.getElementById('desc');" +
            "d.value = " + JSON.stringify(args.description) + ";" +
            "d.dispatchEvent(new Event('input', { bubbles: true }));" +
            "document.getElementById('run').click(); return true; })()",
          returnByValue: true,
        })
        .catch((err) => {
          throw new Error("填写案情失败：" + err.message);
        });
    }
    await client.send("Page.startScreencast", {
      format: "png",
      quality: 90,
      maxWidth: args.width,
      maxHeight: args.height,
      everyNthFrame: 1,
    });

    const start = Date.now();
    const deadline = start + args.maxSeconds * 1000;
    let done = false;
    while (Date.now() < deadline) {
      await sleep(1000);
      const result = await client
        .send("Runtime.evaluate", {
          // 结束条件：任务不再处于「分析中」，且结论卡已经渲染出来
          expression:
            "JSON.stringify({badge:((document.getElementById('stateBadge')||{}).textContent||'')," +
            "summary:!!(document.getElementById('summaryCard')" +
            "&&!document.getElementById('summaryCard').classList.contains('d-none'))})",
          returnByValue: true,
        })
        .catch(() => null);
      let snapshot = { badge: "", summary: false };
      try {
        snapshot = JSON.parse((result && result.result && result.result.value) || "{}");
      } catch (err) {
        /* 忽略解析失败，下一轮再试 */
      }
      if (
        snapshot.badge &&
        !snapshot.badge.includes("分析中") &&
        snapshot.badge !== "未开始" &&
        snapshot.summary
      ) {
        console.log(`[record] 任务结束且结论已渲染，状态徽章：${snapshot.badge}`);
        done = true;
        break;
      }
    }
    if (!done) console.log("[record] 到达最长录制时长，按当前画面收尾");
    await sleep(args.tailSeconds * 1000);

    await client.send("Page.stopScreencast").catch(() => {});
    client.close();
    fs.writeFileSync(
      path.join(framesDir, "timeline.json"),
      JSON.stringify({ url: args.url, frames }, null, 2),
      "utf-8"
    );
    console.log(`[record] 共 ${frames.length} 帧，用时 ${((Date.now() - start) / 1000).toFixed(1)}s`);
    return { frames, framesDir };
  } finally {
    chrome.kill();
  }
}

function encode(framesDir, outFile, targetSeconds, ffmpeg) {
  const timeline = JSON.parse(
    fs.readFileSync(path.join(framesDir, "timeline.json"), "utf-8")
  );
  const frames = timeline.frames;
  if (frames.length < 2) throw new Error("帧太少，无法编码");

  const span = frames[frames.length - 1].t - frames[0].t;
  const hold = Math.max(0, Number(arguments[4]) || 6);
  // 按真实节奏做适度加速：把「过程 + 结尾定格」一起压到目标时长
  const speed = Math.min(3, Math.max(1, (span + hold) / targetSeconds));
  // 用硬链接把「按真实间隔重复的帧」排成固定帧率序列，再用 image2 读，
  // 这样时长完全可控（concat 的 duration 会被末条忽略、总时长会飘）
  const seqDir = `${framesDir}-seq`;
  fs.rmSync(seqDir, { recursive: true, force: true });
  fs.mkdirSync(seqDir, { recursive: true });
  const fps = 25;
  let seqIndex = 0;
  const pushFrame = (file) => {
    seqIndex += 1;
    const target = path.join(seqDir, `seq-${String(seqIndex).padStart(6, "0")}.png`);
    try {
      fs.linkSync(path.join(framesDir, file), target);
    } catch (err) {
      fs.copyFileSync(path.join(framesDir, file), target);
    }
  };
  // 累积器：把「不足一帧」的零头带到下一帧，否则每帧都会被至少重复一次、总时长翻倍
  let carry = 0;
  let emitted = 0;
  for (let i = 0; i < frames.length; i += 1) {
    const current = frames[i];
    const next = frames[i + 1] || { t: current.t + 1 / fps };
    const duration = Math.max(1 / 120, (next.t - current.t) / speed);
    carry += duration * fps;
    let repeats = Math.floor(carry);
    carry -= repeats;
    if (emitted === 0) repeats = Math.max(1, repeats);
    for (let n = 0; n < repeats; n += 1) {
      pushFrame(current.file);
      emitted += 1;
    }
  }
  // 结尾定格几秒，让人看清最后的结论与补证清单
  const lastFrame = frames[frames.length - 1].file;
  const holdRepeats = Math.max(fps, Math.round((hold / speed) * fps));
  for (let n = 0; n < holdRepeats; n += 1) {
    pushFrame(lastFrame);
  }

  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  const result = spawn(
    ffmpeg,
    [
      "-y",
      "-framerate",
      String(fps),
      "-i",
      path.join(seqDir, "seq-%06d.png"),
      "-vf",
      `scale=${1600}:-2:flags=lanczos`,
      "-r",
      String(fps),
      "-c:v",
      "libx264",
      "-preset",
      "medium",
      "-crf",
      "23",
      "-pix_fmt",
      "yuv420p",
      "-movflags",
      "+faststart",
      outFile,
    ],
    { stdio: ["ignore", "pipe", "pipe"] }
  );
  return new Promise((resolve, reject) => {
    let stderr = "";
    result.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    result.on("close", (code) => {
      if (code === 0) resolve({ speed, seconds: (span + hold) / speed });
      else reject(new Error(`ffmpeg 退出码 ${code}\n${stderr.slice(-800)}`));
    });
  });
}

(async () => {
  const args = parseArgs(process.argv);
  // 传 --frames <目录> 时只重新编码，不重新录制
  const framesDir = args.frames || (await captureFrames(args)).framesDir;
  const outFile = path.resolve(`${args.out}.mp4`);
  const { speed, seconds } = await encode(
    framesDir,
    outFile,
    args.target,
    args.ffmpeg,
    args.hold
  );
  console.log(
    `[encode] ${outFile}（加速 ${speed.toFixed(2)}×，成片约 ${seconds.toFixed(1)}s）`
  );
})().catch((err) => {
  console.error("[record] 失败：", err.message);
  process.exit(1);
});
