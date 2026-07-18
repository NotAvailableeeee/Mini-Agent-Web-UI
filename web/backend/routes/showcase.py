"""GET /api/showcase/cards — list the 6 capability cards shown on the home page.

Cards are aligned with the demos documented in README_CN:
1. 生成网页
2. 分析 CSV 数据
3. 生成 PDF 文档
4. 网页搜索与摘要
5. 重构代码
6. 写 README / 整理资料
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class ShowcaseCard(BaseModel):
    id: str
    icon: str
    title: str
    description: str
    prompt: str
    tags: List[str] = []


CARDS: List[ShowcaseCard] = [
    ShowcaseCard(
        id="html-page",
        icon="🌐",
        title="生成网页",
        description="让 Agent 写一个 HTML 文件并在浏览器内嵌预览",
        prompt=(
            "请在当前工作区生成一个漂亮的 index.html，主题是 "
            "'Mini-Agent 能力展示页'，包含 hero 区域和 3 个能力卡片。"
        ),
        tags=["Skill: WriteTool", "输出: HTML"],
    ),
    ShowcaseCard(
        id="csv-analysis",
        icon="📊",
        title="分析 CSV 数据",
        description="读 CSV + Python 处理 + 生成可视化 HTML 报告",
        prompt=(
            "请先创建一个示例 sales.csv（30 天销量数据），"
            "再写一个 Python 脚本分析并生成 HTML 可视化报告（含一张图表）。"
        ),
        tags=["Skill: BashTool + Python", "输出: HTML"],
    ),
    ShowcaseCard(
        id="pdf-doc",
        icon="📄",
        title="生成 PDF 文档",
        description="调用 document-skills 输出专业 PDF 文档",
        prompt=(
            "请生成一份关于 Mini-Agent 的 PDF 介绍文档，"
            "包含项目简介、核心能力、3 个使用示例。"
        ),
        tags=["Skill: document-skills", "输出: PDF"],
    ),
    ShowcaseCard(
        id="web-search",
        icon="🔍",
        title="网页搜索与摘要",
        description="调 MCP 工具搜索网络信息并总结成摘要",
        prompt=(
            "请搜索 'Agentic AI 最新进展'，"
            "然后用 300 字以内的中文给我一段摘要。"
        ),
        tags=["Skill: MCP", "输出: 文本摘要"],
    ),
    ShowcaseCard(
        id="refactor",
        icon="🛠️",
        title="重构代码",
        description="用 Read/Edit 工具改造已有 Python 项目",
        prompt=(
            "请在当前工作区创建一个简单的 Python 脚本（计算斐波那契数列），"
            "然后重构它为带类型注解和文档字符串的版本。"
        ),
        tags=["Skill: WriteTool + EditTool", "输出: Python"],
    ),
    ShowcaseCard(
        id="write-readme",
        icon="📝",
        title="写 README / 整理资料",
        description="把零散资料整理为结构化 Markdown",
        prompt=(
            "请创建几个示例 .txt 笔记文件，"
            "然后把它们的内容整合成一个结构化的 README.md。"
        ),
        tags=["Skill: WriteTool", "输出: Markdown"],
    ),
]


@router.get("/cards", response_model=List[ShowcaseCard])
async def list_cards() -> List[ShowcaseCard]:
    """Return the home-page showcase cards."""
    return CARDS