# scripts/ 数据工具脚本

## legacy/ — 历史一次性数据处理脚本（非运行时依赖，插件加载不需要）
- generate_grimoire_data.py — 从 Anima-Tools JS 生成魔导书词库（v4.0 前后）
- convert_smartcomfy.py / clean_sc_data.py / reorg_data.py / split_subcats.py — 早期词库清洗与分类拆分
- gen_k2_data.py / gen_k2_inject.py — K2 词库生成与注入

这些脚本完成任务后已归档；如需再次清洗词库，参考各文件头部注释（数据源与输出路径可能需要改）。

## 运行时注意
插件运行只依赖仓库根的 main.py / anima_data.py / random_prompt.py / data_paths.py / gitee_sync.py
与本目录无关。deploy.sh 不部署本目录。
