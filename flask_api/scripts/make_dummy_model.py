"""Generate a tiny fake model bundle (model.onnx + tokenizer.json + model_meta.json)
for exercising the ONNX code path before Intern 1 ships a real model. Scores are meaningless.

    pip install onnx
    python scripts/make_dummy_model.py models/current
"""
import json, sys, numpy as np, onnx
from onnx import helper, TensorProto, numpy_helper
from tokenizers import Tokenizer, models, pre_tokenizers
out = sys.argv[1]
import os; os.makedirs(out, exist_ok=True)
X = helper.make_tensor_value_info("input_ids", TensorProto.INT64, [1, "seq"])
M = helper.make_tensor_value_info("attention_mask", TensorProto.INT64, [1, "seq"])
Y = helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, 2])
W = numpy_helper.from_array(np.array([[-0.5, 0.5]], dtype=np.float32), "W")
nodes = [helper.make_node("Cast", ["input_ids"], ["xf"], to=TensorProto.FLOAT),
         helper.make_node("Cast", ["attention_mask"], ["mf"], to=TensorProto.FLOAT),
         helper.make_node("Mul", ["xf", "mf"], ["xm"]),
         helper.make_node("ReduceMean", ["xm"], ["r"], axes=[1], keepdims=1),
         helper.make_node("MatMul", ["r", "W"], ["logits"])]
g = helper.make_graph(nodes, "t", [X, M], [Y], [W])
m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)]); m.ir_version = 8
onnx.save(m, f"{out}/model.onnx")
tok = Tokenizer(models.WordLevel({"[UNK]": 0, "fine": 1, "scam": 9}, unk_token="[UNK]"))
tok.pre_tokenizer = pre_tokenizers.Whitespace()
tok.save(f"{out}/tokenizer.json")
json.dump({"version": "dummy-0", "labels": ["safe", "violation"], "safe_label": "safe", "max_length": 64}, open(f"{out}/model_meta.json", "w"))
