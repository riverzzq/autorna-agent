# AutoRNA — 项目总结

_更新到 2026-11-19。所有数字都来自真实运行记录，不是估计值。_

## 1. 做了什么

AutoRNA 是一个干实验 RNA-seq 自动化项目：自然语言输入 → Nextflow + 容器跑 Salmon 定量 → 监控、失败处理、质控 → DESeq2 差异分析 → 每 run 一页报告，运行记录进 SQLite。技术栈：Nextflow DSL2、FastQC、Salmon、MultiQC、DESeq2、Python/FastAPI、SQLite。公开数据，不用 PSC 数据，不需要 Ivan 批准。

| 阶段 | 时间 | 内容 |
|---|---|---|
| Week 1 | 2026-11-10 – 11-16 | Nextflow 流程 FastQC → Salmon → MultiQC（docker / singularity / standard / test 四个 profile）；agent 骨架（FastAPI + SQLite）；第一次真实运行跑通；推到 GitHub（首 commit `0756e94`，Public，main 分支） |
| Week 2 | 2026-11-16 – 11-18 | 结构化任务单端到端跑通；规则版自然语言解析；接通 OpenAI LLM 解析（gpt-4o-mini，规则版做 fallback），返回里带 `parser: llm / rule`，看得出是哪一个解析的 |
| Week 3 | 2026-11-18 | QC 解析器（`GET /qc`）；OOM 自动重跑一次；DESeq2 接线（`POST /deseq2`）；每 run 一页 HTML 报告（`GET /report`） |
| 页面版 | 2026-11-18 | 网页 UI（`/ui`）+ 自动生成的接口文档（`/docs`），不用 terminal 也能用 |
| 数据集理解第 1 步 | 2026-11-18 | 新数据集先检查再提议：扫 FASTQ 文件夹、查 SRA 元数据、推断分组、提议对比，拿不准就问，不猜 |

GitHub 现状（2026-11-19 核实）：`github.com/riverzzq/autorna-agent`，Public，2 个 commit，最新 `ab5f1ad`（Week 2+3 已推）。页面版和数据集理解的代码已写好，还没装到本地，也还没推。

## 2. 输入是什么

- **自然语言请求**，例如 `run the yeast test` / `跑酵母测试`；或者**结构化任务单** JSON（samples、contrast、genome、profile）。
- **测试数据**：酵母 3v3，WT（SRR6357070–72）vs RAP1 缺失 30 分钟（SRR6357076–78），每文件约 5 万条读数，参考转录组是酿酒酵母 R64-1-1 的抽样版（只含 124 个转录本），GEO GSE110004。
- **新数据集**：本地 FASTQ 文件夹路径，和/或 SRA 编号（例如 airway 数据的 SRR1039508–12）。
- **运行环境**：MacBook Air、Docker Desktop、Nextflow 26.10.6、R 4.4.1 + tximport/DESeq2；LLM 解析需要 `OPENAI_API_KEY`，没 key 时自动用规则版。

## 3. 产出是什么

- **流程产物**：6 个样本的 `quant.sf`、`results/test/multiqc/multiqc_report.html`。
- **任务记录**：SQLite 里每 run 一行（id、样本、对比、状态、耗时、qc_pass、错误信息），接口可查：`GET /jobs`、`/jobs/{id}`、`/qc`、`/deseq2`、`/report`。
- **QC 结果（run-9c783ca0，真实数据）**：6 个样本比对率 79.4%–81.2%，读数 39,686–40,615，中位数 40,354.5，MultiQC 表齐，`qc_pass = 1`，零 fail。
- **DESeq2 结果（run-9c783ca0，真实数据）**：124 个基因，tx2gene 124 对，显著 1 个（padj < 0.05 且 |log2FC| > 1），前 5 名按 padj 排是 YAR009C、YAR010C、YAL038W、YAL003W、YAL037C-B，全部在 RAP1 缺失组下调；产物 `results.tsv`、`volcano_data.tsv`、`volcano.pdf`、`session_info.txt`；耗时 3.8 秒。
- **报告**：`runs/run-9c783ca0/report.html`，一页里有 QC 表、DESeq2 摘要、前 5 基因、MultiQC 链接。
- **数据集检查结果（airway，真实 ENA 元数据）**：全是人（Homo sapiens）、双端，分组是 untreated、Dex、Alb、Alb_Dex 共 4 组；因为是多因素设计，没有硬凑一个对比，而是问跑哪一组对比；人的参考基因组还没接线。

## 4. 解决办法（遇到的问题 → 怎么解决的）

| 问题 | 解决办法 |
|---|---|
| Nextflow 26 严格解析器不接受顶层 `workflow.onComplete {}` | 移到 workflow 内部做 `onComplete:` |
| Salmon 容器 tag `hdfd78af_0` 不存在 | 改成实际存在的 `h7e5ed60_0`（`docker pull` 验证过） |
| Salmon 报错 `RapMap-based indexing is not supported` | 去掉 `--type quasi`，只留 pufferfish 索引 |
| 只跑出 1 个样本的定量（其余 5 个没跑） | Salmon 索引 channel 是 queue channel、只能消费一次，改成 `.first()` 变成 value channel |
| MultiQC 报错缺 `multiqc_data` | MultiQC 1.19 实际写的是 `multiqc_report_data`，改输出路径 |
| LLM 解析悄悄失败、回落到规则版，查不出原因 | 加 `[planner]` 日志行；根因是 httpx/brotli 解码错误（`output_buffer_limit`），改成自定义 httpx client、只接受 gzip/deflate，并把 brotli 升到 ≥1.2.0 |
| 第一次启动失败：连不上 Docker daemon | 不是代码问题，是 Docker Desktop 没开；打开后重跑成功（39.9 秒） |
| 重跑成功后还显示第一次失败的错误 | 成功时把 error 字段清掉（`error=None`） |
| R 里没有 tximport | `BiocManager::install(c("tximport", "DESeq2"))` |
| 推 GitHub 前怕把敏感文件带上去 | 先 `git status --short` 检查，确认只有代码文件；`.gitignore` 已排除 results/、work/、runs/、data/test/、data/reference/、data/airway/、runs.db、.env |

## 5. 局限性（诚实写，不夸大）

- **只是测试数据**：参考转录组是抽样版（124 个转录本），每样本约 4 万条读数，所以只测了 124 个基因、只显著 1 个。这证明的是整条线能跑通，不是一份有生物学意义的结果。
- **只有一条固定流程**：现在只会酵母 3v3 这一条线；人的参考基因组（GRCh38）还没接线，FASTQ 和参考基因组还不能自动下载。
- **OOM 自动重跑写好了，但从来没在真实 OOM 上触发过**，只在本地用逻辑验证过。
- **LLM 解析只在酵母 3v3 上可靠**：规则版 fallback 只认识这一个 case；超出范围的自由输入会被拒绝。LLM 也可能翻错，校验加 fallback 只能降低风险，不能根除。
- **只在本地跑**：MacBook + Docker Desktop，没有上云、没有 HPC 部署。
- **链特异性字段只是记录**：Salmon 实际用 `-l A` 自动判断，samplesheet 里的值不影响结果。
- **OpenAI key 在截图里出现过明文**，已经提醒过两次要轮换，至今没确认换过。
- **页面版和数据集理解的代码还没装、没推**，线上还是 Week 3 的版本。
- **README 里还有一块 Week 1 的旧 TODO**，写着 LLM 和 DESeq2 是 stub，跟下面已打勾的 Week 3 内容对不上，等改注释时一起清。

## 6. 下一步的 solution

1. **装包加推送**：装页面版（0.6.0）和数据集理解包（0.7.0），推到 GitHub；以后改一点推一次，别攒一大波再推。
2. **注释清理**：按她的要求集体改一遍代码里的注释，顺手清掉 README 里那块旧 TODO。
3. **第 2 步（自适应执行）**：按物种自动下载参考基因组（Ensembl）和 FASTQ（SRA），再把 airway 人类数据集从头到尾跑通一次。
4. **第 3 步（规划型 agent）**：一句话任务自动拆步骤、含糊输入先反问、失败了读日志自己换办法再试。
5. **换掉暴露过的 OpenAI key**。
6. **写进简历**：诚实的说法是“把自然语言请求翻译成校验过的流程任务、带自动质控和失败处理的 agent”，配上真实数字（6 样本、比对率 79.4%–81.2%、124 基因、1 显著），不声称通用 agent、不声称生物学发现。
