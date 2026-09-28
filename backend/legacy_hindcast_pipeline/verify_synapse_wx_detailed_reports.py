import json
from pathlib import Path
import pandas as pd

OUT = Path(__file__).resolve().parents[2] / "outputs"
validation = pd.read_csv(OUT / "synapse_wx_validation_detailed_metrics.csv")
test = pd.read_csv(OUT / "synapse_wx_final_test_detailed_metrics.csv")
frozen = json.loads((OUT / "synapse_wx_model_only_adaptive_report.json").read_text(encoding="utf-8"))
labels = {"GFS": "GFS", "IFS HRES": "IFS HRES", "AIFS": "AIFS",
          "Equal-weight blend": "equal_weight_blend",
          "SYNAPSE-WX adaptive trust": "adaptive_model_trust"}
expected_models = {*labels, "Static inverse-MAE blend"}
assert len(validation) == 234 and len(test) == 258
assert set(validation.model) == set(test.model) == expected_models
assert set(validation.loc[validation.metric_family == "event", "threshold_mm"]) == {1., 10., 25., 50.}
assert set(test.loc[test.metric_family == "event", "threshold_mm"]) == {1., 10., 25., 50.}
prediction_districts = set(pd.read_csv(OUT / "synapse_wx_model_only_validation_predictions.csv").district)
assert set(validation.loc[validation.scope_type == "district", "scope_value"]) == prediction_districts
assert set(test.loc[test.scope_type == "month", "scope_value"]) == {"2026-05", "2026-06", "2026-07", "2026-08"}
for label, key in labels.items():
    vr = validation[(validation.scope_type == "overall") & (validation.model == label)].iloc[0]
    tr = test[(test.scope_type == "overall") & (test.model == label)].iloc[0]
    assert abs(vr.mae_mm - frozen["validation_metrics"][key]["mae_mm"]) < 0.001
    assert abs(tr.mae_mm - frozen["final_test_metrics"][key]["mae_mm"]) < 0.001
    assert abs(vr.bias_mm_unrounded - frozen["validation_metrics"][key]["bias_mm_unrounded"]) < 1e-12
    assert abs(tr.bias_mm_unrounded - frozen["final_test_metrics"][key]["bias_mm_unrounded"]) < 1e-12
required = "Final held-out test evaluation. No test data was used for model selection or hyperparameter tuning."
assert required in (OUT / "synapse_wx_final_test_report.md").read_text(encoding="utf-8")
print(json.dumps({"status": "pass", "validation_metric_rows": len(validation),
                  "test_metric_rows": len(test),
                  "validation_scopes": validation.groupby("scope_type").size().to_dict(),
                  "test_scopes": test.groupby("scope_type").size().to_dict(),
                  "aifs_test_bias_full_precision": frozen["final_test_metrics"]["AIFS"]["bias_mm_unrounded"],
                  "required_statement_present": True}, indent=2))
