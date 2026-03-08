import json, pytest

def test_rapor_example_schema():
    r = json.load(open("examples/rapor.example.json"))
    assert r.get("varlıklar"), "no assets"
    v = r["varlıklar"][0]["veri"]
    assert "ATR_14" in v
    assert "SMA_200" in v
    assert "ana_trend" in v

def test_final_karar_example_schema():
    f = json.load(open("examples/final_karar.example.json"))
    k = f["kararlar"][0]
    assert "atr_degeri" in k
    assert "stop_loss" in k
    assert "take_profit" in k

def test_legends_weights_sum():
    # 8 efsane ağırlıkları: 20+18+15+12+10+10+8+7 = 100
    weights = [20, 18, 15, 12, 10, 10, 8, 7]
    assert sum(weights) == 100
