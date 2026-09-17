# 案理 · 项目文档索引

> 本目录用于「杭州智能体大赛 · AI+超级智能体赛道」参赛作品。
> 文档结构参考 Obsidian 笔记《Agent 设计教程》第三章「项目级规范文档」。

## 产品一句话

用户描述一起民事纠纷，选一个身份（原告 / 被告 / 法官视角），智能体完成从案情理解、法条与类案检索、双方对抗辩论、挑刺修订，到输出「我的诉求能不能实现、最多能到多少」的可执行方案的端到端流程。

## 文档地图（按阅读顺序）

| 文档 | 回答的问题 | 稳定性 |
| --- | --- | --- |
| [PRD.md](./PRD.md) | 为什么做、为谁做、做什么、怎么算做完 | 中高 |
| [APP_FLOW.md](./APP_FLOW.md) | 用户怎么走一遍、异常时发生什么、状态怎么变 | 中高 |
| [FRONTEND_GUIDELINE.md](./FRONTEND_GUIDELINE.md) | 前端长什么样、组件遵守什么规则 | 高 |
| [BACKEND_STRUCTURE.md](./BACKEND_STRUCTURE.md) | 后端怎么分层、数据怎么流动 | 高 |
| [CLAUDE.md](./CLAUDE.md) | 编码 Agent 在本仓库应该怎么干活 | 高 |
| [PROGRESS.md](./PROGRESS.md) | 当前做到哪、下一步是什么 | 低，持续更新 |

## 使用约定

- 开工前只需读与本轮任务相关的文档，不要一次性把整套文档加载进上下文。
- PRD 只回答「要什么」，技术实现一律写在 BACKEND_STRUCTURE。
- PROGRESS.md 是唯一的进度真相来源，换人或换会话先读它。
- 需求或架构发生变化时，先更新对应文档，再改代码。

## 怎么运行

**方式一：双击 exe（推荐给非开发同学）**

```
dist\案理.exe
```

- 双击后会弹出黑色窗口并自动打开浏览器，地址是 http://127.0.0.1:8765/
- 黑色窗口保持开着；关掉窗口即停止服务。
- **交付包不含任何密钥**：`dist\.env` 已从包中剥离。首次运行请把根目录的
  `.env.example` 复制一份改名为 `dist\.env`，填入自己的密钥；没有密钥时程序自动退回规则版，
  功能照常可用，只是案情理解与论证质量会下降。
- 端口被占用时程序会自动顺延到 8766 并打开对应地址。

**界面上的两个必填项**

- **适用地区**：民事裁判尺度各省不同，地区决定类案区间取哪里的样本，默认浙江省。
  本地区样本少于 3 件时才会参考外省案例，并在结论里写明混入了几件。
- **身份**：原告 / 被告 / 法官视角，决定立场、措辞与统计口径。

**环境开关**

| 变量 | 作用 |
| --- | --- |
| `ANLI_LLM_API_KEY` / `ANLI_LLM_BASE_URL` / `ANLI_LLM_MODEL` | 大模型配置，缺失时自动退回规则版 |
| `ANLI_DISABLE_LLM=1` | 强制不调用大模型 |
| `ANLI_DISABLE_WEB=1` | 不注册「官方库核对」工具（断网演示时用），过程流里不会出现空跑的检索 |
| `ANLI_PORT` / `ANLI_NO_BROWSER` | 指定端口 / 不自动打开浏览器（自动化测试用） |

运行时会在项目根目录写 `state/`（任务检查点）与 `cache/`（官方库核对缓存），两者都不进安装包。

**成本与耗时**

每次分析的结论摘要末尾会输出一行「模型调用 N 次、失败 M 次、耗时 T 秒」。
想看到金额，需要配置单价——在项目根目录放 `pricing.json`（可复制下面的模板），
或设环境变量 `ANLI_PRICE_INPUT_PER_MILLION` / `ANLI_PRICE_OUTPUT_PER_MILLION`：

```json
{
  "input_per_million": 2,
  "output_per_million": 8,
  "currency": "CNY"
}
```

**没配单价时只报次数与耗时，不估算金额**——不编一个看起来专业的数字。
配了单价但接口没返回 token 用量时，同样如实写「无法估算」。

**模型连接状态**

页头右上角常驻显示模型状态：`大模型已连接 · deepseek-chat` / `大模型未连接 · 规则版` /
`大模型已关闭 · 规则版`。掉线时任务照常完成，但会在分析进度卡顶部显示
「已掉线：本次为规则版分析」，避免把规则版结果误当成大模型结论。

**版本管理**

项目根目录已 `git init` 并持续提交；`.gitignore` 排除 `.env`、`dist/.env`、`state/`、`cache/`、
`cases/`、`build/`、`dist/`、`artifacts/`，以及判决书提取草稿 `data/draft_从文书提取.json`，
密钥与运行数据不会进库。许可证见 [LICENSE](./LICENSE)（MIT）。

**用链接直接打开某次任务**

分析完成后浏览器会记住任务号，刷新页面会自动恢复上次结果；也可以直接把任务号拼进地址：

```
http://127.0.0.1:8765/?task=7b8cccc0cddc     # 打开指定任务（服务重启后仍可用）
http://127.0.0.1:8765/?demo=car&role=defendant&region=江苏省
http://127.0.0.1:8765/?fresh=1              # 忽略上次任务，从空白开始
```

## 更新官方案例缓存

类案参照里的真实案例来自最高人民法院官网「指导性案例」栏目，由抓取工具写入本地缓存：

```powershell
python tools/fetch_cases.py                 # 默认抓 12 页列表（约 20 篇指导性案例）
python tools/fetch_cases.py --pages 34      # 抓更多页
python tools/fetch_cases.py --list-only     # 只看列表，不抓正文
```

产物是 `data/cases.official.json`，每条都带案号、官方链接与抓取时间。
刑事案件一律不入库；官方案例只做裁判要点参照，不参与金额区间计算。

## 录制 30 秒演示视频

成片在 `artifacts/demo-30s.mp4`。重录用 `tools/record_demo.js`（Chrome 真实画面 + ffmpeg 编码），
命令与注意事项见 [DEMO_SCRIPT.md](./DEMO_SCRIPT.md) 第六节。

**方式二：双击启动脚本（需要本机有 Python）**

```
启动案理.cmd
```

**方式三：命令行**

```powershell
python -m anli.web          # 启动服务并打开浏览器
python -m anli.cli demo     # 直接跑一遍主线案例
```

## 重新打包 exe

```powershell
python -m PyInstaller --noconfirm --onefile --console --name 案理 `
  --add-data "web;web" --add-data "data;data" app.py
Copy-Item .env dist\.env -Force
```
