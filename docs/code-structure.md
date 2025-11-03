# 代码结构说明

本文档说明重构后的代码组织结构。

## 目录结构

```
src/git_tool/
├── __init__.py          # 模块导出
├── server.py            # MCP 接口定义（只包含 @server.tool() 装饰器）
├── models.py            # 数据模型定义（Cmd, GitInput, FlowAction, WorkLogInput 等）
├── git_commands.py      # git 工具的实现逻辑
├── git_flow_commands.py # git_flow 工具的实现逻辑
├── git_gitwork_commands.py # git_work 工具的实现逻辑
├── git_combos.py        # Git 组合命令模板（已存在）
└── prompt_profiles.py  # 提示词配置文件（已存在）
```

## 文件职责

### `server.py` - MCP 接口定义

**职责**：
- 定义 MCP 服务器实例（`server` 和 `app`）
- 使用 `@server.tool()` 装饰器定义工具接口
- 参数验证和类型注解
- 调用实现模块执行实际逻辑

**关键特点**：
- 只包含接口定义，不包含具体实现
- 参数描述和文档字符串
- 委托给实现模块处理业务逻辑

### `models.py` - 数据模型

**职责**：
- 定义所有数据模型类（Pydantic BaseModel）
- 定义枚举类型（Cmd, FlowAction, FlowProvider, DiffScope）
- 参数验证规则

**包含的模型**：
- `Cmd` - Git 命令枚举
- `GitInput` - git 工具的输入模型
- `FlowAction` - git_flow 操作类型枚举
- `FlowProvider` - LLM 提供者枚举
- `DiffScope` - diff 范围枚举
- `GitFlowInput` - git_flow 工具的输入模型
- `WorkLogProvider` - git_work AI 提供者枚举
- `WorkLogInput` - git_work 工具的输入模型

### `git_commands.py` - git 工具实现

**职责**：
- 实现所有 `_map_*` 函数（命令参数映射）
- 实现 `run_git` 函数（执行 Git 命令）
- 实现 `execute_git_command` 函数（主要入口，包含异常处理）

**关键函数**：
- `_map_status`, `_map_add`, `_map_commit` 等 - 将结构化参数映射为 Git 命令行参数
- `run_git` - 执行 Git 子进程并返回结果
- `execute_git_command` - 主要执行函数，包含完整的异常处理

### `git_flow_commands.py` - git_flow 工具实现

**职责**：
- 实现 LLM 提供者调用逻辑
- 实现上下文收集（README、diff、status）
- 实现提示词构建和格式化
- 实现 `execute_git_flow_command` 函数（主要入口，包含异常处理）

**关键函数**：
- `_call_provider` - 调用 LLM API
- `_build_context` - 收集仓库上下文
- `_format_prompt` - 格式化提示词
- `_handle_git_flow` - 执行 git_flow 操作的核心逻辑
- `execute_git_flow_command` - 主要执行函数，包含完整的异常处理

### `git_gitwork_commands.py` - git_work 工具实现

**职责**：
- 实现本地仓库提交收集（`_get_commits_between`）
- 实现远程仓库提交收集（GitHub/Gitee）
- 实现工作会话计算（`_compute_work_sessions`）
- 实现并行工作时间检测（`_detect_parallel_sessions`）
- 实现 AI 总结生成（`_generate_summary_with_llm`）
- 实现 Markdown 渲染（单项目/多项目模式）
- 实现 `execute_work_log_command` 函数（主要入口，包含异常处理）

**关键函数**：
- `_get_commits_between` - 从本地仓库获取指定时间范围的提交
- `_get_github_events` / `_get_gitee_events` - 从远程仓库获取提交
- `_get_commit_numstat` - 获取提交的统计信息（文件、增删行数）
- `_compute_work_sessions` - 基于提交时间计算工作会话
- `_detect_parallel_sessions` - 检测跨项目的并行工作时间段
- `_generate_summary_with_llm` - 使用 LLM 生成工作总结
- `_render_markdown_gitwork` / `_render_multi_project_gitwork` - 渲染 Markdown 格式的工作日志
- `execute_work_log_command` - 主要执行函数，包含完整的异常处理

## 代码组织优势

### 1. 关注点分离

- **接口层**（`server.py`）：专注于 MCP 协议和接口定义
- **业务层**（`git_commands.py`, `git_flow_commands.py`, `git_gitwork_commands.py`）：专注于业务逻辑实现
- **数据层**（`models.py`）：专注于数据模型和验证

### 2. 易于维护

- 每个文件职责单一，易于理解和修改
- 实现逻辑与接口定义分离，便于测试
- 新增工具只需添加新的实现文件，不影响现有代码

### 3. 可扩展性

- 新增 Git 命令：只需在 `git_commands.py` 中添加新的映射函数
- 新增 git_flow 操作：只需在 `git_flow_commands.py` 中扩展
- 新增 git_work 功能：只需在 `git_gitwork_commands.py` 中扩展
- 新增数据模型：在 `models.py` 中添加

### 4. 可测试性

- 实现函数可以独立测试，不依赖 MCP 框架
- 可以轻松创建单元测试和集成测试
- Mock 接口定义层来测试实现层

## 依赖关系

```
server.py
  ├── models.py (数据模型)
  ├── git_commands.py (git 实现)
  ├── git_flow_commands.py (git_flow 实现)
  │     ├── git_commands.py (使用 run_git)
  │     ├── models.py (使用数据模型)
  │     ├── git_combos.py (组合命令模板)
  │     └── prompt_profiles.py (提示词配置)
  └── git_gitwork_commands.py (git_work 实现)
        ├── models.py (使用数据模型)
        ├── GitPython (使用 Repo)
        └── requests (GitHub/Gitee API)
```

## 使用示例

### 直接调用实现函数（用于测试）

```python
from src.git_tool.models import GitInput, Cmd
from src.git_tool.git_commands import execute_git_command

# 创建输入对象
payload = GitInput(
    repo_path="/path/to/repo",
    cmd=Cmd.status,
    args={},
    dry_run=False,
    allow_destructive=False,
    timeout_sec=30
)

# 执行命令
result = execute_git_command(payload)
print(result)  # JSON 字符串
```

### 通过 MCP 接口调用（生产环境）

```python
from src.git_tool.server import git, git_flow, git_work

# 通过 MCP 工具调用
result = git(
    repo_path="/path/to/repo",
    cmd="status",
    args={}
)

# 生成工作日志
work_log_result = git_work(
    repo_paths=["/path/to/repo"],
    days=7,
    add_summary=True
)
```

## 重构历史

- **重构前**：所有代码都在 `server.py` 中（1266 行）
- **重构后**：
  - `server.py` - 约 400 行（仅接口定义，包含三个工具）
  - `models.py` - 约 210 行（数据模型，包含 WorkLogInput）
  - `git_commands.py` - 约 480 行（git 实现）
  - `git_flow_commands.py` - 约 530 行（git_flow 实现）
  - `git_gitwork_commands.py` - 约 1000 行（git_work 实现）

## 注意事项

1. **相对导入**：所有模块使用相对导入（`from .xxx import`），确保包结构正确
2. **异常处理**：实现函数已经包含完整的异常处理，接口层只需简单的委托
3. **类型提示**：所有函数都有完整的类型注解，便于 IDE 支持和类型检查
4. **向后兼容**：通过 `__init__.py` 导出，保持原有导入方式兼容

