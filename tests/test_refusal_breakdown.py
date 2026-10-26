from tushen_kb.eval.refusal_breakdown import CATS, classify, spliced


def test_classify_rules():
    assert classify({"refusal_reason": "生成模型判定证据不足", "llm_called": True}) == (CATS[0], None)
    assert classify({"refusal_reason": "生成答案中的引文未通过原文校验", "llm_called": True, "n_bad_quotes_stage": 1}) == (CATS[1], None)
    assert classify({"refusal_reason": "生成答案未给出原文引文", "llm_called": True, "n_quotes_stage": 0}) == (CATS[2], None)
    assert classify({"refusal_reason": "证据不足（rerank 最高分 0.512 < 阈值 0.94）", "llm_called": False}) == (CATS[3], "S1闸门")
    assert classify({"refusal_reason": "没有通过原文校验的引用", "llm_called": False}) == (CATS[3], "无通过校验的引用")
    assert classify({"refusal_reason": "生成模型判定证据不足", "llm_called": False}) == (CATS[3], "未能分类")
    assert classify({"refusal_reason": "未检索到相关条文"}) == (CATS[3], "其他原因")


def test_spliced():
    assert spliced("「甲……乙」") and not spliced("「甲乙」")
