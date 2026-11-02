# Agent 开发系列 · 可运行示例

这个仓库按组件组织配套代码。每个组件目录都独立可运行、独立说明，后续新增的工具、记忆、评测等内容会继续按同样结构加入。

## 组件索引

| 目录 | 主题 | 内容 | 说明 |
| --- | --- | --- | --- |
| [`loop/`](loop/README.md) | Agent Loop | 最小工具循环、可控宿主、DeepSeek Tool Calls、报表工具、28 项离线测试 | [loop/README.md](loop/README.md) |

## 仓库约定

- 每个组件目录自包含，运行命令默认在该组件目录内执行。
- 组件运行产物统一写入各自的 `runs/`，不进入版本库。
- `.env` 只保存在本地，API Key 不提交、不写日志。
- 根目录只放仓库级索引和通用 `.gitignore`，具体实现与说明都放在组件目录内。

## 当前入口

```bash
cd loop
python lesson.py --all
```

详细文件作用、运行方式和边界说明见 [loop/README.md](loop/README.md)。
