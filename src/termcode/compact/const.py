"""上下文管理的内部预算，统一按 UTF-8 字节估算输入。"""

# 单条工具结果的落盘阈值。
SINGLE_RESULT_LIMIT = 50000
# 单条工具消息的未替换结果聚合预算。
MESSAGE_AGGREGATE_LIMIT = 200000
# 为摘要输出预留的 token 数。
SUMMARY_RESERVE = 20000
# 自动摘要的估算误差余量。
AUTO_SAFETY_MARGIN = 13000
# 摘要预检与紧急重试的安全余量。
MANUAL_SAFETY_MARGIN = 3000
# 恢复段最多展示的文件数。
RECOVERY_FILE_LIMIT = 5
# 单个文件快照的 token 预算。
RECOVERY_TOKENS_PER_FILE = 5000
# 近期原文必须同时达到 token 与消息数下界。
RECENT_KEEP_TOKENS = 10000
RECENT_KEEP_MESSAGES = 5
# 自动摘要连续失败的熔断次数。
MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES = 3
# 摘要超长后逐组丢弃的重试次数。
PTL_RETRY_LIMIT = 3
# 后续重试每次丢弃的剩余消息组比例。
PTL_DROP_PERCENTAGE = 0.2
# 字节增量转 token 的估算比例。
ESTIMATE_CHARS_PER_TOKEN = 3.5
# 工具预览同时受字节与行数上限约束。
PREVIEW_HEAD_BYTES = 2048
PREVIEW_HEAD_LINES = 20
