from __future__ import annotations

from enum import Enum
from typing import Final, Literal


class CategoryTier(str, Enum):
    DIRECT_ALLOWED = "direct_allowed"
    GATED_ALLOWED = "gated_allowed"
    BLOCKED = "blocked"


DIRECT_ALLOWED: Final[frozenset[str]] = frozenset({
    "person", "author", "named_nonhuman", "work_title", "document_title",
    "location", "facility", "organization", "company", "government_body",
    "group", "brand", "product_model", "vehicle_name", "named_event",
})

GATED_ALLOWED: Final[frozenset[str]] = frozenset({
    "person_alias", "entity_alias", "fixed_person_title", "official_rank",
    "medical_device", "drug_name", "diagnosis_name", "medical_procedure",
    "domain_device", "fictional_species", "fictional_faction", "ability_name",
    "artifact_name", "system_term", "currency_unit", "era_calendar",
})

BLOCKED: Final[frozenset[str]] = frozenset({
    "anatomy", "body_part", "body_fluid", "body_state", "mental_state", "action",
    "generic_technique", "onomatopoeia", "interjection", "adjective", "adverb",
    "descriptive_phrase", "metaphor", "euphemism", "slang", "dialogue_phrase",
    "honorific_generic", "occupation_generic", "kinship_generic", "common_object",
    "clothing_generic", "food_generic", "material_generic", "general_noun",
    "general_medical", "cultural_explanation", "grammar", "pronoun", "number_datetime",
    "translation_variant", "plot_fact", "ocr_uncertain", "unresolved",
})

CATEGORY_VALUES: Final[tuple[str, ...]] = tuple(sorted(DIRECT_ALLOWED | GATED_ALLOWED | BLOCKED))
Category = Literal[
    "person", "author", "named_nonhuman", "work_title", "document_title", "location",
    "facility", "organization", "company", "government_body", "group", "brand",
    "product_model", "vehicle_name", "named_event", "person_alias", "entity_alias",
    "fixed_person_title", "official_rank", "medical_device", "drug_name", "diagnosis_name",
    "medical_procedure", "domain_device", "fictional_species", "fictional_faction",
    "ability_name", "artifact_name", "system_term", "currency_unit", "era_calendar",
    "anatomy", "body_part", "body_fluid", "body_state", "mental_state", "action",
    "generic_technique", "onomatopoeia", "interjection", "adjective", "adverb",
    "descriptive_phrase", "metaphor", "euphemism", "slang", "dialogue_phrase",
    "honorific_generic", "occupation_generic", "kinship_generic", "common_object",
    "clothing_generic", "food_generic", "material_generic", "general_noun",
    "general_medical", "cultural_explanation", "grammar", "pronoun", "number_datetime",
    "translation_variant", "plot_fact", "ocr_uncertain", "unresolved",
]

SOURCE_SCOPES: Final[frozenset[str]] = frozenset({"body", "title", "author", "cover", "front_matter"})
BODY_SOURCE_SCOPE: Final[str] = "body"

# Legacy values are accepted only at the compatibility boundary and are immediately
# rewritten.  They are not part of the v3 category set.
LEGACY_CATEGORY_ALIASES: Final[dict[str, str]] = {
    # English aliases & plurals
    "character": "person",
    "characters": "person",
    "people": "person",
    "place": "location",
    "places": "location",
    "title": "work_title",
    "family_name": "person_alias",
    "person_name": "person",
    "medical": "medical_procedure",
    "item": "artifact_name",
    "occupation": "occupation_generic",
    "honorific": "honorific_generic",
    "anatomy": "anatomy",
    "body": "body_part",
    "technique": "generic_technique",
    "term": "system_term",
    "terminology": "system_term",
    "other": "unresolved",
    "general": "unresolved",
    # Chinese category names produced by LLMs
    "人物": "person",
    "人名": "person",
    "角色": "person",
    "角色名": "person",
    "人物名": "person",
    "人": "person",
    "地点": "location",
    "场所": "location",
    "地名": "location",
    "地点名": "location",
    "位置": "location",
    "设施": "facility",
    "建筑": "facility",
    "建筑物": "facility",
    "组织": "organization",
    "机构": "organization",
    "单位": "organization",
    "公司": "company",
    "企业": "company",
    "政府机构": "government_body",
    "政府部门": "government_body",
    "团体": "group",
    "群体": "group",
    "家族名": "group",
    "家族": "group",
    "家系": "group",
    "解剖": "anatomy",
    "身体部位": "body_part",
    "部位": "body_part",
    "器官": "body_part",
    "生殖器": "body_part",
    "性器官": "body_part",
    "体液": "body_fluid",
    "阵营": "group",
    "势力": "group",
    "品牌": "brand",
    "商标": "brand",
    "产品型号": "product_model",
    "型号": "product_model",
    "载具": "vehicle_name",
    "交通工具": "vehicle_name",
    "车辆": "vehicle_name",
    "事件": "named_event",
    "事件名": "named_event",
    "重大事件": "named_event",
    "人物别名": "person_alias",
    "别名": "person_alias",
    "外号": "person_alias",
    "绰号": "person_alias",
    "爱称": "person_alias",
    "昵称": "person_alias",
    "实体别名": "entity_alias",
    "固定头衔": "fixed_person_title",
    "人物称谓": "fixed_person_title",
    "称谓": "fixed_person_title",
    "头衔": "fixed_person_title",
    "称号": "fixed_person_title",
    "爵位": "fixed_person_title",
    "尊称": "fixed_person_title",
    "职务": "official_rank",
    "官职": "official_rank",
    "军衔": "official_rank",
    "职级": "official_rank",
    "医疗器具": "medical_device",
    "医疗器械": "medical_device",
    "医疗设备": "medical_device",
    "医疗用具": "medical_device",
    "药品": "drug_name",
    "药物": "drug_name",
    "药名": "drug_name",
    "诊断": "diagnosis_name",
    "病名": "diagnosis_name",
    "疾病": "diagnosis_name",
    "病症": "diagnosis_name",
    "医疗流程": "medical_procedure",
    "医疗操作": "medical_procedure",
    "医疗程序": "medical_procedure",
    "手术": "medical_procedure",
    "治疗": "medical_procedure",
    "专业设备": "domain_device",
    "专用设备": "domain_device",
    "虚构种族": "fictional_species",
    "种族": "fictional_species",
    "物种": "fictional_species",
    "虚构势力": "fictional_faction",
    "门派": "fictional_faction",
    "帮派": "fictional_faction",
    "能力": "ability_name",
    "技能": "ability_name",
    "招式": "ability_name",
    "法术": "ability_name",
    "魔法": "ability_name",
    "道具": "artifact_name",
    "宝物": "artifact_name",
    "物品": "artifact_name",
    "神器": "artifact_name",
    "法宝": "artifact_name",
    "系统术语": "system_term",
    "专有名词": "system_term",
    "设定术语": "system_term",
    "术语": "system_term",
    "名词": "system_term",
    "游戏": "system_term",
    "规则": "system_term",
    "器物名": "common_object",
    "器物": "common_object",
    "服饰": "clothing_generic",
    "服装": "clothing_generic",
    "食物": "food_generic",
    "材料": "material_generic",
    "货币单位": "currency_unit",
    "货币": "currency_unit",
    "币种": "currency_unit",
    "纪元": "era_calendar",
    "年号": "era_calendar",
    "历法": "era_calendar",
    "时代": "era_calendar",
    "状态": "plot_fact",
}


def canonical_category(value: object) -> str:
    category = str(value or "").strip().casefold()
    return LEGACY_CATEGORY_ALIASES.get(category, category)


def category_tier(category: object) -> CategoryTier | None:
    canonical = canonical_category(category)
    if canonical in DIRECT_ALLOWED:
        return CategoryTier.DIRECT_ALLOWED
    if canonical in GATED_ALLOWED:
        return CategoryTier.GATED_ALLOWED
    if canonical in BLOCKED:
        return CategoryTier.BLOCKED
    return None


def is_known_category(category: object) -> bool:
    return category_tier(category) is not None


def has_independent_support(term: object) -> bool:
    """Require recurrence across paragraphs or chapters before activation."""
    if not isinstance(term, dict):
        return False
    evidence = [item for item in term.get("evidence", []) if isinstance(item, dict)]
    paragraphs = {str(item.get("paragraph_id", "")).strip() for item in evidence if item.get("paragraph_id")}
    chapters = {str(item.get("chapter_id", "")).strip() for item in evidence if item.get("chapter_id")}
    return len(paragraphs) >= 2 or len(chapters) >= 2
