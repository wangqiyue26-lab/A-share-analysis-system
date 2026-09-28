from ashare_system.config import load_yaml


def test_load_system_config():
    config = load_yaml("config/system.yml")
    assert config["project"]["timezone"] == "Asia/Shanghai"
    assert config["data"]["provider_order"][:2] == [
        "akshare_eastmoney",
        "akshare_sina",
    ]
