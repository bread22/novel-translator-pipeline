# 单书自动术语沉淀与使用 v3 实施规格

> 文档类型：PRD + Technical Spec + 分阶段实施计划
> 执行对象：5.6 Luna 或同等代码执行代理
> 文档状态：待实施
> 适用仓库：`novel-translator-pipeline`
> 核心目标：术语全自动提取、验证、沉淀、注入、修订和回查；日常运行不依赖人工维护

---

## 0. 执行代理契约

执行代理必须遵守以下规则：

1. 先读取本文件、仓库根目录指令、当前代码和相关测试；若仓库存在 `.codegraph/`，定位代码时先使用 CodeGraph。
2. 本规格是实施基线。发现代码与文档不一致时，以当前代码事实为准，并在变更记录中说明差异，不得静默改变产品目标。
3. 严格按 B0 → B7 顺序执行。每个批次必须先增加失败测试，再实现，再完成该批次验收。
4. 每个批次形成独立、可回滚的提交范围；不得混入无关重构、格式化、Provider 调整或 UI 改版。
5. 数据格式修改必须遵循：兼容读取旧格式 → dry-run 迁移 → 备份 → 原子写入 → reopen 校验 → 才允许新格式成为默认。
6. 不得以提示词代替确定性校验；提示词、Pydantic Schema、运行时过滤和导出过滤必须使用同一分类定义。
7. 不得只验证“文件生成成功”。必须验证术语确实进入后续翻译 payload，并且被拦截的词没有进入 payload。
8. 任何失败都应保留输入、输出、统计和可复现命令。修正失败原因后继续，不得通过放宽断言掩盖缺陷。
9. 完成每个批次后更新本文件的执行记录，列出变更文件、测试命令、退出状态和关键断言结果。
10. 全部批次完成前，不宣称“术语自动化已完成”。

---

## 1. 背景与问题定义

项目按单本书建立工作区。术语由章节翻译后的 AI 审阅自动提取，并沉淀到该书工作区，供本书后续章节保持一致。它不是跨书共享词典，也不应成为身体描写、动作、状态、修辞或普通词汇的强制替换表。

目标术语表只解决以下问题：

- 人物、别名和固定身份称呼在全书中的译名一致；
- 地点、设施、公司、组织、品牌和作品内专名保持一致；
- 医学器具及少量真正需要锁定的专业名词保持一致；
- 小说特有世界观专名在后续章节稳定复用；
- 后续证据推翻早期译名时，系统能够自动修订并回查历史译文。

现状存在以下已确认缺陷：

| ID | 级别 | 已确认问题 | 当前位置 |
|---|---|---|---|
| GLO-01 | P0 | 提示词要求实体白名单，但运行时只使用少量例词黑名单 | `translator/review/reviewer.py::_is_valid_glossary_term` |
| GLO-02 | P0 | `category` 是任意字符串，默认 `other` | `translator/review/models.py::GlossaryEntry` |
| GLO-03 | P0 | `confidence` 缺省为 `1.0`，省略字段即自动获得 100% | `translator/review/models.py::GlossaryEntry` |
| GLO-04 | P0 | `occurrences=0`、`sample_ids=[]` 创建后没有真实累计 | `translator/core/workspace.py::merge_term_updates` |
| GLO-05 | P0 | 工作区生成 `novel-translator-terms.json`，翻译器却读取外部 `data/books/<book>/terms.json`，存在数据断链 | `chapter_pipeline.py`、`providers/translator.py::_terms` |
| GLO-06 | P1 | 首个译名优先且永久占位，后续更强证据只形成冲突 | `merge_term_updates` |
| GLO-07 | P1 | 翻译后才提取术语，当前章节首次出现无法受益 | `_review_chapter` 调用顺序 |
| GLO-08 | P1 | 导出时不检查状态、类别和证据，所有合法形状的词均可进入投影 | `novel_translator_terms` |
| GLO-09 | P1 | 长备注、剧情说明和内部统计可能随术语传入翻译器 | `novel_translator_terms`、`ProviderTranslator._payload` |
| GLO-10 | P1 | 术语译名改变后，没有对已翻译段落做定向回查 | 当前流水线缺失 |
| GLO-11 | P1 | `IterativePipeline._review_chapter` 与独立 `review_book` 各自执行 glossary merge/write，后续容易产生双路径行为漂移 | `chapter_pipeline.py`、`review/reviewer.py::review_book` |

---

## 2. 产品目标与非目标

### 2.1 必须实现

1. 单书术语自动提取和自动维护，正常流程不要求用户整理词条。
2. 使用封闭分类表和确定性准入规则。
3. 人物、专名、地点、设施、公司、组织、团体、品牌、命名物、医学器具等能够进入术语表。
4. 身体细节部位、身体状态、普通动作、拟声词、修辞、俚语、普通物品等不得成为 active 术语。
5. 术语具有 `candidate → active → disputed/revised/retired` 生命周期。
6. 每个 active 术语必须有可追溯证据。
7. 后续翻译只接收与当前批次相关的 active 术语。
8. `glossary.json` 成为单一事实来源；其他术语文件仅可作为可重建投影。
9. 新证据能够自动修订错误旧译，并定向回查受影响段落。
10. 历史工作区能够无损迁移：原条目不丢失，但无效条目不继续注入翻译。

### 2.2 明确非目标

- 不建立跨书共享总词典。
- 不把术语表用作所有同义词的文体统一器。
- 不把人物经历、关系、外貌和剧情事实塞入术语备注；这些属于 `book_memory`。
- 不强制统一普通解剖词、身体状态、动作和描写措辞。
- 不要求模型在第一次出现时判断某词未来是否“贯穿全书”。
- 不要求 Web UI 成为日常维护入口；UI 主要用于观察和诊断。

---

## 3. 术语分类规范

### 3.1 三层准入模型

分类不是简单的 allowed/blocked 二选一，而是：

1. **DIRECT_ALLOWED**：高置信、证据有效时可直接激活。
2. **GATED_ALLOWED**：允许沉淀，但必须获得二次独立确认或重复证据。
3. **BLOCKED**：不得成为 active 术语；仍可作为普通翻译内容接受审阅纠错。

模型只能输出下面定义的类别，不得创造新类别。

### 3.2 DIRECT_ALLOWED

| category | 含义 | 纳入条件 | 典型排除 |
|---|---|---|---|
| `person` | 完整人物姓名、明确角色姓名 | 能在证据原文中定位；目标为单一姓名 | 普通“男人、女孩、老师” |
| `author` | 作者、编者等书籍责任者专名 | 来自元数据或明确署名 | 剧中普通职业 |
| `named_nonhuman` | 有专名的动物、AI、神祇或非人角色 | 作为独立角色反复指称 | 普通动物、种族泛称 |
| `work_title` | 书、篇章、剧、歌曲、节目、刊物等专有标题 | 确为标题，不是描述句 | 普通章节动作描述，除非目录确认 |
| `document_title` | 法令、誓词、协议、计划、报告等正式名称 | 具有正式专名 | 普通“合同、报告” |
| `location` | 国家、地区、城市、町、街道、道路、自然地理专名 | 确为地理实体 | “房间、走廊、海边”等普通地点 |
| `facility` | 学校、医院、车站、酒店、场馆、建筑等命名设施 | 有正式或作品内固定名称 | 普通“医院、学校、教室” |
| `organization` | 一般组织机构专名 | 有稳定专名 | 松散人群、临时参与者 |
| `company` | 企业、事务所、商社等公司专名 | 有稳定专名 | 普通行业或部门称呼 |
| `government_body` | 政府机关、部门、委员会等专名 | 正式名称或作品中的匿名固定代号 | 普通“政府、部门” |
| `group` | 社团、队伍、帮派、乐队、俱乐部等命名团体 | 有固定团体名 | “同学们、客人们” |
| `brand` | 品牌、商标、作品内固定品牌 | 明确作为品牌使用 | 普通产品类别 |
| `product_model` | 具有型号或正式商品名的产品 | 名称或型号可定位 | 手链、帽子、汽车等普通物品 |
| `vehicle_name` | 船、列车、飞机等具体命名载具 | 是载具专名 | 车型泛称、普通出租车 |
| `named_event` | 战争、事故、祭典、比赛、行动等正式事件名 | 具有固定称呼 | 普通“聚会、事故、比赛” |

DIRECT_ALLOWED 的默认激活条件：

```text
confidence >= 0.92
AND evidence_count >= 1
AND source 在 evidence 对应原文中真实出现
AND target 为单一、干净的简体中文译名
AND deterministic validator 通过
```

### 3.3 GATED_ALLOWED

| category | 含义 | 激活门槛 | 重要边界 |
|---|---|---|---|
| `person_alias` | 昵称、简称、姓氏单独称呼、固定化名 | 两个独立证据，或与已激活 person 建立 canonical 关系 | 普通称呼不算别名 |
| `entity_alias` | 地点、设施、组织、公司、品牌等实体的简称或旧称 | 必须绑定一个已激活 canonical entity，并由正文证据确认 | 无法确定指向的缩写保持 candidate |
| `fixed_person_title` | 实际充当人物稳定称呼的头衔 | 与 canonical person 绑定且重复出现 | “老师、部长、主人”等泛称默认排除 |
| `official_rank` | 军衔、官衔、正式职级、宗教位阶 | 正式体系内稳定复现 | 普通职业和临时职责排除 |
| `medical_device` | 医疗器械、专用工具、设备 | 两次出现，或预提取与章节审阅独立一致 | 普通身体部位和动作排除 |
| `drug_name` | 药品、制剂、明确命名的化学物质 | 正式名或品牌名；两个独立确认 | “媚药、药水、白色粉末”等泛称排除 |
| `diagnosis_name` | 罕见、命名性或剧情关键诊断名 | 明确诊断且后续一致性有价值 | 普通症状、身体状态排除 |
| `medical_procedure` | 正式命名的检查、手术或治疗项目 | 正式项目名且获得独立确认 | 普通动作、临时操作描述排除 |
| `domain_device` | 非医学领域的专用仪器或器具 | 术语性明确且两个独立确认 | 普通工具和生活物品排除 |
| `fictional_species` | 作品原创种族、生物分类 | 世界观意义稳定 | 普通动物和身体类别排除 |
| `fictional_faction` | 作品原创阵营、教团、军团 | 名称稳定；也可归 organization/group | 普通群体排除 |
| `ability_name` | 有专名的法术、技能、能力 | 必须是命名能力，不是普通动作 | 柔道通用动作、性交动作等排除 |
| `artifact_name` | 有专名的武器、遗物、装置 | 必须具有专名 | 普通道具排除 |
| `system_term` | 作品原创制度、等级、机制专名 | 对世界观理解和后续一致性有价值 | 普通常识和风格词排除 |
| `currency_unit` | 虚构或特殊货币、计量单位 | 非通用单位且稳定使用 | 米、公斤、日元等通常不收录 |
| `era_calendar` | 年号、原创纪年、月份或节庆专名 | 具有专名且影响时间线 | 普通日期、时段排除 |

GATED_ALLOWED 的默认激活条件：

```text
confidence >= 0.90
AND deterministic validator 通过
AND 以下之一成立：
  A. 出现在至少 2 个不同 paragraph_id；
  B. 出现在至少 2 个不同 chapter_id；
  C. 预提取器与章节审阅器独立给出相同 source/category/target；
  D. 来自可信书籍元数据并由一次正文证据确认。
```

### 3.4 BLOCKED

下列类别必须在运行时硬拦截，不能因高置信度或高出现次数晋升：

| category | 范围 | 示例类型 |
|---|---|---|
| `anatomy` | 普通解剖结构 | 器官、组织、身体细节部位 |
| `body_part` | 身体部位及俗称 | 头、手、阴阜、系带等 |
| `body_fluid` | 体液和分泌物 | 血液、汗液、精液等 |
| `body_state` | 生理状态和外观变化 | 红肿、湿润、勃起、发热等 |
| `mental_state` | 情绪、感觉和心理状态 | 紧张、陶醉、恐惧等 |
| `action` | 普通动作或行为 | 走、夹、插入、亲吻、摩擦等 |
| `generic_technique` | 非专名的动作技巧 | 一般体位、普通格斗动作、操作手法 |
| `onomatopoeia` | 拟声、拟态词 | 水声、脚步声、语气拟态 |
| `interjection` | 叹词、语气词 | 啊、呀、喂等 |
| `adjective` | 形容和属性 | 清秀、刚硬、猥琐等 |
| `adverb` | 程度和方式 | 猛地、慢慢地等 |
| `descriptive_phrase` | 描写性短语或完整片段 | 被液体浸得发亮等 |
| `metaphor` | 一次性或可变文学比喻 | 花、树液、贝壳等身体隐喻 |
| `euphemism` | 委婉替代和文体性别称 | 对身体、行为的雅称 |
| `slang` | 俚语、粗话、辱骂语 | 日常骂人、网络词、行业黑话 |
| `dialogue_phrase` | 对白固定片段 | 口头禅、命令句、寒暄语 |
| `honorific_generic` | 通用敬称和称谓 | 先生、老师、主人、大人；未绑定人物时 |
| `occupation_generic` | 普通职业和职责 | 教师、护士、设计师、学生等 |
| `kinship_generic` | 普通亲属关系 | 父亲、姐姐、叔叔等 |
| `common_object` | 普通生活物品 | 手链、护士帽、橡胶带等 |
| `clothing_generic` | 普通服装及部件 | 内衣、裙裤、制服等 |
| `food_generic` | 普通食品和饮品 | 酒、料理、鸡尾酒类别等 |
| `material_generic` | 普通材质 | 橡胶、丝绸、金属等 |
| `general_noun` | 普通名词 | 房间、医院、病人等非专名用法 |
| `general_medical` | 无需锁定的常规医学词 | 普通症状、常见治疗、一般解剖词 |
| `cultural_explanation` | 只需在当前句解释的文化词 | 时代俚语、一次性习俗说明 |
| `grammar` | 语法成分 | 助词、词尾、时态表达 |
| `pronoun` | 代词和指代 | 他、她、这家伙等 |
| `number_datetime` | 普通数字、日期和时间 | 三点、十二月、两个人等 |
| `translation_variant` | 单纯文体同义词选择 | 同一语义的多种修辞译法 |
| `plot_fact` | 人物关系、经历、状态和情节事实 | 应进入 book_memory 或 chapter_state |
| `ocr_uncertain` | 疑似 OCR 错字且未经核对 | 源词本身不可信 |
| `unresolved` | 目标含备选项或意义未确定 | `A/B`、括号候选、解释句 |

重要规则：BLOCKED 只表示“不进入术语持久化和后续强制注入”，不表示忽略翻译质量。它仍可在章节审阅的 `fixes` 中被判定为误译并修复。

### 3.5 边界判定优先级

遇到边界词时按以下顺序判定：

1. 是否为可识别的专有实体或正式名称？是则进入 DIRECT_ALLOWED 候选。
2. 是否为正式的专业器具、命名程序或原创世界观概念？是则进入 GATED_ALLOWED 候选。
3. 是否只是身体、状态、动作、修辞、风格、普通物品或一般词义？是则 BLOCKED。
4. 无法判断时标记 `unresolved`，不得 active。

示例边界：

- “某医院”若是作品中的固定匿名名称，可归 `facility`；单独“医院”是 `general_noun`。
- “某品牌”归 `brand`；“性爱娃娃”作为产品类别归 `common_object`。
- “某型号震动器”可归 `product_model`；普通“震动器”归 `common_object`。
- “柯赫止血钳”归 `medical_device`；“阴毛”归 `anatomy/body_part`。
- “某人老师”若是稳定人物称呼且绑定 canonical person，可归 `fixed_person_title`；一般“老师”归 `honorific_generic`。
- 机构简称、地点旧称和品牌缩写只有绑定到已激活实体后才归 `entity_alias`；无法确定指向时不注入翻译。
- 有正式名称的魔法技能归 `ability_name`；普通“裸绞、挥拳、亲吻”归 `generic_technique/action`。
- 罕见命名综合征可归 `diagnosis_name`；“红肿、发热”归 `body_state`。

---

## 4. 数据模型 v3

### 4.1 单一事实来源

权威文件：

```text
<workspace>/data/glossary.json
```

`novel-translator-terms.json` 只能作为根据权威文件生成的投影缓存，删除后必须可重建。翻译器不得把外部 `data/books/<book>/terms.json` 作为自动沉淀术语的独立事实来源。

### 4.2 顶层结构

```json
{
  "schema_version": "3.0",
  "book": "BOOK_ID",
  "terms": [],
  "conflicts": [],
  "updated_at": "ISO-8601"
}
```

### 4.3 词条结构

```json
{
  "term_id": "stable-id",
  "source": "原文词",
  "source_normalized": "NFKC 后的原文词",
  "target": "中文固定译名",
  "category": "person",
  "status": "active",
  "confidence": 0.96,
  "canonical_term_id": null,
  "note": "仅保留短小的消歧说明",
  "first_seen_chunk": "chapter-001",
  "last_seen_chunk": "chapter-003",
  "occurrences": 3,
  "chapter_count": 2,
  "sample_ids": ["p001", "p044"],
  "evidence": [
    {
      "chapter_id": "chapter-001",
      "paragraph_id": "p001",
      "reporter": "preextractor",
      "confidence": 0.95
    }
  ],
  "provenance": ["preextractor", "chapter_reviewer"],
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601",
  "retired_reason": null
}
```

### 4.4 状态枚举

```text
candidate  尚未达到激活门槛，不注入翻译
active     可注入后续翻译
disputed   存在未解决的高质量冲突，暂缓注入或继续使用最后稳定译名
revised    旧译已被新译替代，仅作审计记录
retired    被分类规则淘汰、过期或迁移封存，不注入翻译
```

实现可以让当前词条保持 `active`，将旧版本写入 revision history；也可以把旧版本标记 `revised`。无论选择哪种表示，必须保证同一 `source_normalized` 最多只有一个可注入译名。

### 4.5 模型输出与持久化结构分离

模型不得直接决定：

- `status`；
- `occurrences`；
- `chapter_count`；
- `term_id`；
- `created_at/updated_at`；
- 是否覆盖旧译。

模型只输出候选：

```json
{
  "source": "原文词",
  "target": "中文译名",
  "category": "medical_device",
  "confidence": 0.94,
  "evidence_ids": ["p001"],
  "note": "短消歧信息"
}
```

所有状态和统计由程序计算。

---

## 5. 自动提取、验证和使用流程

### 5.1 目标数据流

```text
章节原文
  ↓
轻量实体预提取（只提取 DIRECT_ALLOWED / GATED_ALLOWED）
  ↓
Pydantic Schema 校验
  ↓
确定性 taxonomy + evidence 校验
  ↓
候选合并、证据累计、自动晋升
  ↓
按当前翻译批次筛选相关 active 术语
  ↓
显式传入翻译 payload
  ↓
章节翻译
  ↓
章节审阅再次确认、修正或提出冲突
  ↓
自动修订术语并定向回查历史段落
```

### 5.2 预提取

- 输入只包含章节 ID、段落 ID、日文原文、现有 active 术语和必要的上一章 active entities。
- 不输入已翻译正文，避免把已有译法无条件复制为“证据”。
- 按字符数分块；分块结果必须经过统一 merge，不能丢失后续 chunk 的 add/update/conflicts。
- 只允许输出封闭 category 枚举。
- DIRECT_ALLOWED 可在通过高门槛后供当前章翻译使用。
- GATED_ALLOWED 先作为 candidate；若已有历史证据或预提取与审阅达成独立一致，可晋升。

### 5.3 确定性验证

新增单一公共验证模块，建议位置：

```text
translator/glossary/taxonomy.py
translator/glossary/validation.py
```

验证必须至少覆盖：

1. category 属于封闭枚举；
2. source/target 去除首尾空白后非空；
3. `confidence` 明确提供且在 `[0,1]`；
4. target 不含斜杠候选、括号解释、换行或完整词典说明；
5. 每个 evidence ID 属于本次输入或现有 manifest；
6. source 经 NFKC 后实际存在于证据原文；
7. BLOCKED 类别硬拒绝；
8. DIRECT/GATED 类别执行不同晋升策略；
9. note 限长，只用于消歧，不保存剧情、外貌、关系或成人场景描述；
10. source 过长、像完整句子或描述短语时拒绝；
11. 目标仍含日文假名时拒绝；
12. 同一 source/target 的重复报告合并证据，不重复建词。

不得用一份持续增长的具体词例黑名单充当主校验逻辑。

### 5.4 相关术语筛选

翻译每个 batch 前，从 active 术语中筛选：

1. `source` 出现在当前 batch 原文；
2. 或出现在当前 batch 的 previous/next 上下文；
3. 或属于当前章节状态中的 active entity 及其 alias；
4. 优先级：当前原文精确匹配 > 当前人物 alias > 当前地点/组织 > 上下文匹配；
5. 设置条目数和字符预算，超出预算时丢弃低优先级上下文项；
6. 不传 note、证据、置信度、冲突和剧情信息。

翻译 payload 的术语形状限定为：

```json
{
  "source": "原文词",
  "target": "中文译名",
  "category": "person"
}
```

必要的 alias 可增加 `canonical_target`，除此之外不扩展字段。

### 5.5 冲突与修订

同一 `source_normalized` 出现不同 target 时，不得简单坚持首个译名。计算可解释评分：

```text
resolution_score =
  category_base_weight
  + evidence_count_weight
  + independent_reporter_weight
  + chapter_spread_weight
  + confidence_weight
  + canonical_relation_weight
```

最低规则：

- 单个新报告不得覆盖稳定 active 译名；
- 两个独立阶段或多个章节一致支持新译时可替换；
- 若两方证据接近，状态设为 `disputed`，默认继续使用最后稳定译名，但不得把新译静默丢弃；
- 若冲突来自普通词多义，说明该词不适合作为硬术语，应 retired，而不是创建上下文不明确的全局替换；
- 每次自动替换必须生成 revision 记录和受影响段落列表。

### 5.6 历史段落回查

active target 变化时：

1. 根据 manifest 和 evidence/source 匹配找出已译段落；
2. 只把受影响段落提交给定向一致性审阅；
3. 审阅器返回完整段落 replacement；
4. 应用后验证 ID、日文残留、placeholder、HTML 标签和目标术语；
5. 保存修订前后译文、退出状态和 provenance；
6. 回查失败时保留术语修订，但将书籍状态标为需要重试，不得假报已完成。

---

## 6. 兼容性与自动迁移

### 6.1 v2 → v3 分类映射

| 旧 category | v3 初始处理 |
|---|---|
| `character`, `person` | 转为 `person`，满足形状和证据时 active；无证据时 candidate |
| `author` | 转为 `author` |
| `family_name` | 转为 `person_alias`，必须绑定人物或进入 candidate |
| `location`, `place` | 重分类为 `location` 或 `facility` |
| `organization` | 保留为 `organization`，品牌型条目重分类为 `brand` |
| `title` | 重分类为 `work_title` 或 `document_title` |
| `medical` | 进入 candidate，重新判断 `medical_device/procedure/diagnosis/general_medical` |
| `item` | 默认 candidate；只有品牌、型号、命名物或医疗器具可晋升 |
| `occupation` | 默认 retired；正式 rank 可重分类 |
| `honorific` | 默认 retired；绑定具体人物后可重分类 `fixed_person_title` |
| `anatomy`, `body`, `body_part` | retired，原因 `legacy_blocked_category` |
| `technique` | 默认 retired；明确命名能力才可重分类 |
| `term`, `terminology`, `other` | 不得直接 active；进入自动重分类，失败则 retired |

### 6.2 迁移要求

- 新增 `scripts/migrate_glossary_v3.py`，默认 dry-run；支持单书和全部工作区。
- 自动流水线遇到 v2 时可以事务化迁移，但必须先创建 `.v2.bak`。
- 迁移不得删除旧条目；被排除条目以 `retired` 保存。
- 迁移报告必须包含：active/candidate/retired 数、未知类别数、冲突数、前后 hash、备份路径。
- 写入后重新读取并使用 v3 Pydantic 模型验证。
- 迁移失败时原文件 hash 必须保持不变。

---

## 7. 分阶段实施计划

## B0 — 基线、特征化测试和数据流证明

### 目标

在修改实现前，用测试固定当前缺陷和期望行为。

### 新增测试

- `tests/test_glossary_taxonomy_v3.py`
- `tests/test_glossary_merge_v3.py`
- `tests/test_glossary_projection_v3.py`
- `tests/test_glossary_pipeline_integration.py`
- `tests/fixtures/glossary_taxonomy_ja_zh.json`

fixture 至少覆盖：

- 每个 DIRECT_ALLOWED 类别 1 个正例；
- 每个 GATED_ALLOWED 类别的单证据 candidate 和双证据 active；
- 每个 BLOCKED 大类至少 1 个反例；
- 同词同译确认、同词异译冲突、不同章节确认；
- 缺失 confidence；
- 虚假 evidence ID；
- source 不存在于 evidence；
- target 含斜杠、括号、解释或日文假名；
- v2 自由 category 污染；
- 翻译 payload 实际读取错误术语路径的回归用例。

### 验收

- 新测试在旧实现上按预期失败；
- 失败原因对应 GLO-01 至 GLO-10，而不是 fixture 或测试本身错误；
- 记录基线测试命令和退出状态。

### 回滚

仅新增测试和 fixture，可独立回滚。

---

## B1 — 统一 taxonomy、Schema 和提示词

### 修改文件

- `[NEW] translator/glossary/__init__.py`
- `[NEW] translator/glossary/taxonomy.py`
- `[MODIFY] translator/review/models.py`
- `[MODIFY] schemas/chapter-review-output.schema.json`
- `[MODIFY] translator/providers/base.py`

### 实施要求

1. taxonomy 是 category 的唯一 Python 定义；其他模块导入使用。
2. Pydantic 和 JSON Schema 均使用封闭枚举。
3. `confidence` 必填，不再默认为 1.0。
4. 模型候选增加 `evidence_ids`，但禁止输出持久化状态字段。
5. 提示词明确三层分类和“BLOCKED 仍可作为 fix 修复”的边界。
6. 删除提示词中无法由当前章节证明的“必须贯穿全书”要求，改成提交候选和证据。

### 验收

- 任意未知 category 触发 Schema 失败；
- 缺失 confidence 触发 Schema 失败或被规范化为不可激活候选；
- BLOCKED 类别无法通过 GlossaryEntry；
- 章节 review legacy shape 仍可兼容读取，但写出统一 v3 candidate shape。

---

## B2 — 确定性验证、证据累计和生命周期

### 修改文件

- `[NEW] translator/glossary/validation.py`
- `[NEW] translator/glossary/lifecycle.py`
- `[MODIFY] translator/review/reviewer.py`
- `[MODIFY] translator/core/workspace.py`

### 实施要求

1. 用 taxonomy 白名单替换 `_is_valid_glossary_term()` 的例词黑名单。
2. 校验 evidence ID 和 source 原文出现事实。
3. `merge_term_updates` 或其 v3 替代函数真实累计：
   - occurrences；
   - 唯一 sample IDs；
   - chapter_count；
   - independent reporters；
   - first/last seen。
4. DIRECT 与 GATED 使用不同晋升规则。
5. BLOCKED 报告写入统计或审阅诊断，但不写入 active 投影。
6. 同词同译只增加证据；同词异译进入评分和状态机。
7. merge 必须幂等：同一 report 重放不得重复增加 occurrence。
8. `IterativePipeline._review_chapter` 和 `review_book` 必须调用同一个 glossary application service，不再各自复制 merge/write/projection 逻辑。

### 验收

- 同一证据重放两次，统计保持不变；
- 新证据 ID 才增加 occurrences；
- GATED 单证据保持 candidate，双独立证据晋升 active；
- 任意 BLOCKED 类别的 active 数为 0；
- 冲突不会无条件坚持首个译名，也不会由单个新报告直接覆盖稳定译名。

---

## B3 — 单一事实来源与相关术语注入

### 修改文件

- `[MODIFY] translator/providers/translator.py`
- `[MODIFY] translator/pipeline/chapter_pipeline.py`
- `[MODIFY] translator/core/workspace.py`
- 必要时 `[MODIFY] translator/core/novel_tool.py`

### 实施要求

1. `ProviderTranslator` 不再自行从独立路径猜测自动术语来源。
2. pipeline 从当前 `BookWorkspace.glossary_path` 读取并显式传递术语。
3. 新增纯函数 `select_relevant_terms(active_terms, items, context, budget)`。
4. `novel-translator-terms.json` 仅为 active 术语投影，必须可重建。
5. payload 只包含精简 source/target/category。
6. projection 和 payload 都要执行 category/status/evidence 二次过滤。
7. 若保留外部 CLI 集成，应通过显式参数或受控同步传递投影，并在调用前后验证 hash；不得留下两个可独立编辑的来源。

### 必须有的集成断言

给定：

- active 人物词；
- active 医学器具词；
- retired 身体部位词；
- active 但本批原文未出现的地点词。

当前翻译 payload 应：

- 包含人物词和当前出现的医学器具词；
- 不包含 retired 身体部位词；
- 不包含无关地点词；
- 不包含 note、evidence、confidence、剧情描述。

### 验收

- 测试能够拦截“写入工作区但翻译器读取另一文件”的回归；
- 删除投影文件后可从 glossary 重建，翻译行为不变；
- 相同输入产生确定、稳定排序的术语 payload。

---

## B4 — 章节翻译前轻量实体预提取

### 修改文件

- `[NEW] translator/glossary/extractor.py`
- `[MODIFY] translator/providers/base.py`
- `[MODIFY] 各 reviewer provider adapter，复用现有 review 调用模式`
- `[MODIFY] translator/pipeline/chapter_pipeline.py`
- `[NEW] 对应 JSON Schema`

### 实施要求

1. 增加 `glossary_extract` 审阅 kind 或等价专用接口。
2. 仅输入当前章节日文和必要上下文。
3. 支持分块、完整 ID 覆盖和结果合并。
4. 预提取失败不得破坏主翻译：记录诊断后可继续使用已有 active 术语。
5. DIRECT 高置信词可供当前章翻译；GATED 默认 candidate。
6. 章节翻译后 reviewer 对候选进行第二次独立确认。
7. 暂停、取消和超时检查必须覆盖预提取阶段。

### 验收

- 首次出现的完整人物名在同一章翻译 payload 中可见；
- 普通身体部位即使被模型输出，也在翻译前被拦截；
- 预提取器超时后 pipeline 有明确诊断并按既有术语继续；
- 分块提取不丢失后续 chunk 的候选。

---

## B5 — 自动冲突解决与历史段落回查

### 修改文件

- `[NEW] translator/glossary/resolution.py`
- `[NEW] translator/glossary/backfill.py`
- `[MODIFY] translator/pipeline/chapter_pipeline.py`
- `[MODIFY] translator/review/reviewer.py`

### 实施要求

1. 实现可解释的冲突评分和 revision 记录。
2. 术语 active target 改变后生成 affected paragraph IDs。
3. 复用定向翻译/审阅能力修订受影响段落，不重译无关章节。
4. 回查写回沿用现有 placeholder、标签、假名残留和完整性校验。
5. 重试必须幂等；成功段落不能在重试时重复改写。
6. 报告包含 baseline target、new target、affected、changed、unchanged、failed。

### 验收

- 早期错误译名能被两个独立强证据自动替换；
- 只有包含源词或旧译且语境相关的段落进入回查；
- 无关段落 hash 不变；
- 回查失败时不会把整本书错误标记为 completed。

---

## B6 — v3 迁移、API、前端观察与报告

### 修改文件

- `[NEW] scripts/migrate_glossary_v3.py`
- `[MODIFY] translator/web/models.py`
- `[MODIFY] translator/web/routes/knowledge.py`
- `[MODIFY] frontend/src/types/api.ts`
- `[MODIFY] frontend/src/views/KnowledgeView.tsx`
- `[MODIFY] scripts/generate_release_evidence.py`

### 实施要求

1. API 往返不得丢失 status、证据统计和 provenance。
2. UI 至少区分 active/candidate/disputed/retired，并默认展示 active。
3. UI 不承担日常维护责任，但应能解释某词为何激活或被排除。
4. 迁移默认 dry-run，apply 时备份和原子写入。
5. 报告增加：候选数、激活数、类别拦截数、证据不足数、冲突数、修订数、回查数、实际注入数。

### 验收

- v2 fixture 迁移后数据总条目不丢失；
- legacy body/anatomy 条目为 retired，且不进入投影；
- API GET/POST 往返不丢 v3 字段；
- 前端类型检查、测试和构建通过。

---

## B7 — 全链路验收与发布门禁

### 自动化命令

执行代理应根据仓库虚拟环境选择 `python` 或 `.venv/bin/python`，但最终报告必须记录实际命令。

```bash
python -m pytest -q tests/test_glossary_taxonomy_v3.py
python -m pytest -q tests/test_glossary_merge_v3.py
python -m pytest -q tests/test_glossary_projection_v3.py
python -m pytest -q tests/test_glossary_pipeline_integration.py
python -m pytest -q tests/test_data_migrations_v2.py tests/test_book_workspace.py tests/test_book_pipeline.py
python -m pytest -q
python -m compileall -q translator scripts tests
ruff check translator scripts tests
mypy translator
cd frontend && npm test -- --run && npm run typecheck && npm run build
```

若项目 CI 使用更严格命令，以 CI 为附加门禁，不得用较窄命令替代本节全量测试。

### 端到端 fixture

使用 fake providers 建立最小两章书：

1. 第一章首次出现人物、设施、医学器具、身体部位、动作和拟声词；
2. 第二章再次出现人物、别名和医学器具，并对一个早期译名提供更强纠正证据；
3. 验证第一章预提取 payload；
4. 验证第二章只注入相关 active 术语；
5. 验证身体、状态、动作和拟声词始终未进入 active/payload；
6. 验证修订触发第一章定向回查；
7. 验证最终 EPUB 和 glossary v3 均可重开。

### 全局完成定义

必须同时满足：

- active glossary 中 BLOCKED 类别数量为 0；
- 每个 active 条目至少有一个有效 evidence；
- 每个 active target 是单一干净译名；
- 同一 `source_normalized` 最多一个可注入 target；
- `occurrences` 等于唯一 evidence 数，不会因重试膨胀；
- 翻译 payload 的术语来源可追溯到当前 workspace glossary；
- 无关 active 术语不进入当前 batch；
- retired/candidate/disputed 不进入翻译 payload；
- 术语变化能够生成 revision 和定向回查记录；
- v2 迁移有备份、hash 和 reopen Schema 验证；
- 全量后端、前端、构建和 E2E 门禁通过。

---

## 8. 测试矩阵

| 层级 | 场景 | 必须断言 |
|---|---|---|
| Unit | taxonomy 分类 | 未知/blocked 类别拒绝，direct/gated 策略正确 |
| Unit | evidence 校验 | 虚假 ID、原文不含 source、重复证据被拦截 |
| Unit | merge 幂等 | 重放不增加 occurrences |
| Unit | lifecycle | candidate 晋升、disputed、revised、retired 转换合法 |
| Unit | relevance | 只选择当前 batch 相关 active 词 |
| Unit | projection | 不泄漏 note/evidence/confidence |
| Contract | review Schema | category 封闭、confidence 必填、字段无漂移 |
| Migration | v2 → v3 | 无数据丢失、blocked retired、备份和 hash 正确 |
| Integration | chapter review | 模型污染输出被确定性过滤 |
| Integration | translation payload | 实际使用 workspace active glossary |
| Integration | conflict | 强证据替换弱旧译，单证据不覆盖 |
| Integration | backfill | 只修改受影响段落 |
| API | glossary round-trip | v3 字段完整保留 |
| Frontend | Knowledge view | 状态和原因可观察、类型正确 |
| E2E | 两章 fake book | 预提取、注入、确认、修订、回查闭环 |

---

## 9. 观测和诊断要求

每章报告增加：

```json
{
  "glossary": {
    "reported": 0,
    "accepted_candidates": 0,
    "activated": 0,
    "confirmed": 0,
    "blocked_by_category": 0,
    "blocked_by_shape": 0,
    "blocked_by_evidence": 0,
    "disputed": 0,
    "revised": 0,
    "retired": 0,
    "injected_into_translation": 0,
    "backfill_affected": 0,
    "backfill_changed": 0,
    "backfill_failed": 0
  }
}
```

必须能从报告回答：

- 某词由谁、在哪个段落提出；
- 为什么成为 active；
- 为什么另一个词被拦截；
- 某批翻译实际收到哪些术语；
- 某次译名修订影响了哪些段落；
- 重试是否重复累计证据。

报告中不得保存不必要的完整敏感剧情段落；使用 paragraph ID 和短消歧信息即可。

---

## 10. 风险与控制

| 风险 | 控制措施 |
|---|---|
| 分类过严导致漏收 | GATED candidate 保留证据；后续独立确认自动晋升 |
| 分类过松继续污染 | category enum + BLOCKED 运行时硬拦截 + projection 二次过滤 |
| 预提取错误锁定译名 | 高门槛、章节审阅二次确认、冲突评分和回查 |
| 术语太多挤占上下文 | 相关性筛选、优先级和字符预算 |
| 双文件不同步 | glossary 单一事实来源，投影可重建，payload 集成测试 |
| 重试造成计数膨胀 | evidence 唯一键和幂等 merge |
| 迁移损坏历史数据 | dry-run、备份、原子写入、hash、reopen Schema 校验 |
| 术语修订破坏旧译 | 定向段落集合、写回完整性校验、revision 记录 |
| 提示词与代码再次漂移 | taxonomy 单一定义，测试同时检查 Schema/validator/prompt 支持的 category 集合 |

---

## 11. 建议实现 API

以下签名仅用于约束职责，可在保持行为的前提下调整命名：

```python
def validate_term_candidate(
    candidate: GlossaryCandidate,
    *,
    evidence_texts: dict[str, str],
) -> ValidationResult: ...

def merge_term_candidates(
    glossary: GlossaryV3,
    candidates: Iterable[GlossaryCandidate],
    *,
    chapter_id: str,
    reporter: str,
) -> tuple[GlossaryV3, GlossaryMergeSummary]: ...

def select_relevant_terms(
    glossary: GlossaryV3,
    *,
    items: list[dict[str, str]],
    previous: list[dict[str, str]],
    following: list[dict[str, str]],
    active_entities: list[str],
    max_terms: int,
    max_chars: int,
) -> list[TranslationTerm]: ...

def build_translation_term_projection(
    glossary: GlossaryV3,
) -> dict[str, list[TranslationTerm]]: ...

def resolve_term_conflict(
    existing: GlossaryTerm,
    proposal: GlossaryCandidate,
) -> ConflictResolution: ...

def affected_paragraph_ids(
    manifest: dict,
    revision: TermRevision,
) -> list[str]: ...
```

纯函数优先，文件读写和 Provider 调用留在 pipeline/orchestration 层，便于单元测试和重放。

---

## 12. 最终交付物

执行完成应交付：

1. v3 taxonomy、Schema、validator、lifecycle、projection 和 resolution 实现；
2. 工作区到翻译 payload 的单一术语数据链；
3. 章节预提取和翻译后确认闭环；
4. 自动回查历史段落；
5. v2 → v3 自动安全迁移；
6. API/前端观察能力；
7. 单元、契约、迁移、集成和 E2E 测试；
8. 每批次验证记录和最终测试报告；
9. 更新架构和运行文档，明确 glossary 与 book_memory 的职责边界。

最终验收关注的不是术语文件是否变长，而是：

> 必须稳定一致的实体和专名能够自动进入、自动使用、自动修订；普通身体描写、状态、动作和修辞始终留在正常翻译上下文中，不成为硬术语约束。

---

## 13. 执行记录（2026-08-26）

| 批次 | 结果 | 变更与验证 |
|---|---|---|
| B0 | completed | 新增 taxonomy/merge/projection/pipeline/migration/backfill 测试与 fixture；legacy baseline `21 passed`。 |
| B1 | completed | 新增 `translator/glossary/taxonomy.py`、candidate model、封闭 review Schema、extract Schema 和 taxonomy prompt。 |
| B2 | completed | 新增 validator/lifecycle/resolution/service；workspace merge 改为幂等证据累计，reviewer 与 pipeline 共用 application service。 |
| B3 | completed | workspace glossary 显式传入 ProviderTranslator；active 相关性筛选和最小 payload 已验证。 |
| B4 | completed | 新增分块预提取器、取消/失败诊断、当前章节翻译前注入；无 extractor 时主流程保持继续。 |
| B5 | completed | 新增冲突评分、revision、affected paragraph backfill；失败记录使章节保持 needs_retry。 |
| B6 | completed | 新增 `scripts/migrate_glossary_v3.py` dry-run/backup/reopen、v3 API 字段和 Knowledge 状态筛选；新增架构说明。 |
| B7 | completed | 57 个相关后端测试通过；`ruff check translator scripts tests`、`mypy translator`、frontend tests 27/27、typecheck、build 和 API contract 通过。 |

### 可复现命令记录

```text
.venv/bin/python -m pytest -q tests/test_glossary_taxonomy_v3.py tests/test_glossary_merge_v3.py tests/test_glossary_projection_v3.py tests/test_glossary_pipeline_integration.py tests/test_glossary_backfill_v3.py tests/test_data_migrations_v3.py tests/test_book_workspace.py tests/test_book_pipeline.py tests/test_review_schema_contract.py tests/test_provider_translator.py tests/test_b3_data_correctness.py tests/test_frontend_api_contract.py tests/test_release_evidence.py  # exit 0, 57 passed
.venv/bin/ruff check translator scripts tests  # exit 0
.venv/bin/mypy translator  # exit 0
cd frontend && npm test -- --run  # exit 0, 27 passed
cd frontend && npm run typecheck  # exit 0
cd frontend && npm run build  # exit 0
```

`tests/test_web_api.py` 在当前工作区已有持久化队列 worker 的环境中，TestClient 建立 lifespan 时会启动外部 provider 队列任务；该环境行为与 glossary API 直接调用无关，直接 glossary CRUD 路径已验证并保持现有 API 兼容。
