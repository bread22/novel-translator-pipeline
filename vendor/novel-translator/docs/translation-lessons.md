# Novel Translator 翻译实战经验

本文记录一部长篇 EPUB 小说完整翻译、修复、打包和实机验证过程中沉淀的经验。后续处理同类小说时，应把这些规则当作默认检查清单，而不是临时补救步骤。

## 交付原则

- 默认交付单语译文；只有用户明确要求或配置指定时才输出双语对照。
- 一部小说对应一个工作记录目录：`../workspace/books/<书籍ID>/`。后台脚本、日志、外部术语表、质量报告、审校材料和交付包都应收纳到该目录下。
- 最终成品应放入用户约定的“已翻译”目录，同时在工作记录目录保留 `delivery/` 交付副本和报告。
- 任何批量修复前先创建快照，尤其是重译、术语替换、人称称呼修复、EPUB 结构修复前。
- 不要把 API Key、模型地址、私有配置写入报告、文档或提交。

## 长篇翻译运行经验

- 长篇任务必须可中断、可续跑。翻译进程应支持 `request-stop`，恢复前先 `task-status`，再 `clear-stop`。
- 失败重试只针对当前仍未成功的段落；历史失败但后来已成功的段落必须自动跳过。
- 并发翻译要在每个批次完成后立即落盘并记录 run，不要等全部并发结束后统一写入。
- 检测到 HTTP 402、Insufficient Balance、余额不足、欠费等错误时，立即停止继续派发请求，不要自动重试。
- `--dry-run` 只能验证计划和报告，不能被当成真实翻译结果。

## 术语和人称称呼

- 术语表导入前必须做人名、地名、组织名、能力名和称呼审查；不要为了让状态变绿而删除有意义的术语。
- 日文敬称 `さん`、`ちゃん`、`くん`、`さま` 不应机械音译成“桑”“酱”“君”。应按关系改为直呼姓名、先生、小姐、前辈、老师、昵称或省略。
- 翻译完成后必须再跑人称称呼校对。检测到 `person_address_issue` 时，不应交付最终版。
- 称呼检测要避免中文误报：`夫君`、`郎君`、`主君`、`君主`、`诸君` 是正常中文；`甜辣酱`、`果酱`、`调味酱`、`碳酸` 也不是敬称残留。
- 对少量明确的称呼残留，可以在快照后做精确替换，例如 `阿尔君 -> 阿尔`、`辛吉君 -> 辛吉`、`米莉丝酱 -> 米莉丝`。替换只应作用于已被质量扫描标记的段落，避免全书误伤。

## 质量修复

- `placeholder_mismatch` 必须为 0；占位符缺失属于硬失败，不应写入缓存。
- `person_address_issue` 必须为 0 后再导出成品。
- `source_residual` 需要分辨真实漏译和拟声词、喘息、符号、章节分隔符等可接受残留。成人向日文拟声词常会触发假阳性，不能只看数量，要抽样确认。
- `terminology_mismatch` 需要结合术语表质量判断。若术语表本身存在多译名或旧译名，要先修术语表，再决定是否重译。
- 导出前至少运行 `translation-status`、`failed-batches`、`quality-report`、`validate-export`。失败批次必须为 0，待译必须为 0。

## EPUB 保真与实机验证

- EPUB 回写必须依赖导入时保存的节点定位，不能按纯文本替换。重复文本很多时，纯文本替换会串段。
- `nav.xhtml` 不能保留在线性阅读顺序中，否则部分手机阅读器会把目录页当第一章正文。
- `toc.ncx` 必须保持默认 NCX 命名空间，不能导出为 `<ns0:ncx>`，否则部分 Android 阅读器会出现 `LoadTocError`。
- OPF 元数据也是交付内容：`dc:title`、`dc:description`、`dc:language` 应随译本更新，手机书籍详情页不能残留日文简介。
- 交付后必须运行 `validate-epub --path <成品.epub> --json`，重点确认：
  - `valid_for_local_open=true`
  - `nav_broken_links=0`
  - `toc_broken_links=0`
  - `nav_empty_anchors=0`
  - `nav_linear_spine_count=0`
  - `spine_missing=0`
  - `mimetype_first=true`
  - `mimetype_uncompressed=true`
  - `toc_prefixed_namespace=false`
  - `metadata_description_source_residual=false`
- 机器校验通过后，还要运行 `open-local --path <成品.epub> --json`。能调起本机阅读器、目录能加载、第一页不是目录正文，才算通过本地打开测试。

## 成品交付检查清单

1. `translation-status` 显示 `pending=0`。
2. `failed-batches` 显示 `failed=0`。
3. `quality-report` 中 `placeholder_mismatch=0`、`person_address_issue=0`。
4. 单语/双语模式符合用户最新要求；默认单语。
5. `package-delivery --monolingual` 或 `--bilingual` 生成交付包。
6. 成品复制到“已翻译”目录。
7. `validate-epub` 结构校验通过。
8. `open-local` 能打开本机阅读器。
9. 工作记录、报告、术语表、风险报告已收纳到该书工作记录目录。
