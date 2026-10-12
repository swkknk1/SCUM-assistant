# SCUM 物品与载具 ID 查询手册 (Item & Vehicle ID Catalog)

欢迎查阅《SCUM (人渣) 专用服务器物品 ID 完整分类手册》。
本目录为服主提供最常用、经过实测可用的蓝图 ID（Blueprint Asset ID），方便您在 `assistant_config.json` 中添加商城商品或一键刷车。

---

## 📑 快速分类索引

点击下方分类查看对应物品的详细 ID 与中文对照表：

1. **[01-载具与配件 (Vehicles)](./01-载具与配件(Vehicles).md)**：所有车辆、摩托、飞机、船只及轮胎、电池等配件。
2. **[02-枪械与近战武器 (Weapons)](./02-枪械与近战武器(Weapons).md)**：重狙、突击步枪、冲锋枪、手枪、冷兵器与武士刀。
3. **[03-弹药与弹匣 (Ammo & Magazines)](./03-弹药与弹匣(Ammo_Magazines).md)**：整盒弹药箱、各类枪械原装弹匣。
4. **[04-护甲防具与背包 (Armor & Backpacks)](./04-护甲防具与背包(Armor_Backpacks).md)**：军用防弹衣、战术头盔、大型登山包、夜视仪。
5. **[05-医疗与药品 (Medical)](./05-医疗与药品(Medical).md)**：急救包、消炎抗生素、止痛药、缝合针、维生素。
6. **[06-工具锁具与建材 (Tools & Building)](./06-工具锁具与建材(Tools_Building).md)**：大工具箱、高级开锁器、密码锁、C4炸药、撬棍、铲子。
7. **[07-食物与饮品 (Food & Drinks)](./07-食物与饮品(Food_Drinks).md)**：MRE军用口粮、各类罐头、大水壶、能量饮料。

---

## 💡 如何在助手中添加商品？

打开 `assistant_config.json`，在 `"items"` 或 `"vehicles"` 下照着格式添加即可：

### 1. 添加普通商品示例 (使用 `#SpawnItem`)
```json
"items": {
    "5": {
        "name": "夜视仪",
        "item_id": "Night_Vision_Goggles",
        "count": 1,
        "cost": 8000,
        "aliases": ["5", "nvg", "夜视仪"]
    }
}
```

### 2. 添加载具示例 (使用 `#SpawnVehicle`)
```json
"vehicles": {
    "4": {
        "name": "拉卡越野车",
        "model": "BPC_Laika",
        "cost": 45000,
        "aliases": ["4", "laika", "越野车"]
    }
}
```
保存后，重启助手即可自动在游戏内生效！
