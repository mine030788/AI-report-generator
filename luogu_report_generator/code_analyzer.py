import re
from collections import Counter
from typing import Any

def analyze_code_style(records: list[dict[str, Any]]) -> dict[str, Any]:
    """
    静态分析用户的 C++ 提交源码
    records: 包含 sourceCode 字段的记录列表
    """
    if not records:
        return {"error": "无代码可供分析"}

    total_codes = 0
    total_lines = 0
    line_counts = []
    
    # 习惯统计
    features = {
        "bits_stdc++": 0,
        "using_namespace_std": 0,
        "define_int_long_long": 0,
        "ios_sync_with_stdio": 0,
        "cin_cout": 0,
        "scanf_printf": 0,
        "struct": 0,
        "auto": 0,
        "range_based_for": 0,
    }
    
    containers = Counter()
    var_names = Counter()
    
    total_comments_lines = 0
    
    # 缩进统计
    indent_types = Counter()

    for record in records:
        code = record.get("sourceCode", "")
        if not code:
            continue
            
        total_codes += 1
        lines = code.split("\n")
        line_count = len(lines)
        total_lines += line_count
        line_counts.append(line_count)
        
        # 统计注释行数
        comment_lines = sum(1 for line in lines if "//" in line or line.strip().startswith("/*") or line.strip().startswith("*"))
        total_comments_lines += comment_lines
        
        # 宏与习惯匹配
        if re.search(r"#include\s*<\s*bits/stdc\+\+\.h\s*>", code):
            features["bits_stdc++"] += 1
        if re.search(r"using\s+namespace\s+std\s*;", code):
            features["using_namespace_std"] += 1
        if re.search(r"#define\s+int\s+long\s+long", code):
            features["define_int_long_long"] += 1
        if re.search(r"ios(?:_base)?::sync_with_stdio\s*\(\s*(?:false|0)\s*\)", code):
            features["ios_sync_with_stdio"] += 1
            
        if re.search(r"\bcin\s*>>", code) or re.search(r"\bcout\s*<<", code):
            features["cin_cout"] += 1
        if re.search(r"\bscanf\s*\(", code) or re.search(r"\bprintf\s*\(", code):
            features["scanf_printf"] += 1
            
        if re.search(r"\bstruct\s+\w+", code):
            features["struct"] += 1
        if re.search(r"\bauto\b", code):
            features["auto"] += 1
        if re.search(r"for\s*\([^;]+:[^;]+\)", code):
            features["range_based_for"] += 1
            
        # 容器匹配
        for container in ["vector", "queue", "priority_queue", "stack", "deque", "set", "map", "unordered_map", "unordered_set", "pair", "bitset"]:
            if re.search(rf"\b{container}\s*<", code):
                containers[container] += 1
                
        # 常见单字母变量名
        for var in re.findall(r"\b(?:int|long long|double|float|char|bool)\s+([a-zA-Z0-9_,\s]+);", code):
            for v in var.split(","):
                v_name = v.strip().split("=")[0].strip()
                v_name = re.sub(r"\[.*?\]", "", v_name) # 去除数组大小
                if v_name:
                    var_names[v_name] += 1

        # 缩进检测 (抽样前50行)
        tab_count = 0
        space_count = 0
        for line in lines[:50]:
            if line.startswith("\t"):
                tab_count += 1
            elif line.startswith("    ") or line.startswith("  "):
                space_count += 1
        if tab_count > space_count * 2:
            indent_types["Tab"] += 1
        elif space_count > tab_count * 2:
            indent_types["Space"] += 1
        elif tab_count > 0 and space_count > 0:
            indent_types["Mixed"] += 1

    if total_codes == 0:
        return {"error": "没有可分析的代码样本"}

    line_counts.sort()
    median_lines = line_counts[total_codes // 2]

    # 将容器按出现频率排序
    top_containers = [k for k, v in containers.most_common(10)]
    top_vars = [k for k, v in var_names.most_common(15) if len(k) <= 5]

    return {
        "total_codes": total_codes,
        "total_lines": total_lines,
        "median_lines": median_lines,
        "comment_density": total_comments_lines / total_lines if total_lines > 0 else 0,
        "features_rate": {k: v / total_codes for k, v in features.items()},
        "top_containers": top_containers,
        "top_vars": top_vars,
        "indent_preference": indent_types.most_common(1)[0][0] if indent_types else "Unknown"
    }

def format_code_analysis(analysis: dict[str, Any]) -> str:
    """格式化为 Markdown"""
    if "error" in analysis:
        return f"**静态代码分析**: {analysis['error']}"

    lines = []
    lines.append("## 源码静态风格分析")
    lines.append(f"- **分析样本数**: {analysis['total_codes']} 份代码")
    lines.append(f"- **代码长度**: 中位数 {analysis['median_lines']} 行")
    lines.append(f"- **注释密度**: {analysis['comment_density'] * 100:.2f}%")
    lines.append(f"- **缩进偏好**: {analysis['indent_preference']}")
    
    lines.append("- **核心习惯**: ")
    features = analysis["features_rate"]
    lines.append(f"  - `#include <bits/stdc++.h>`: {features.get('bits_stdc++', 0)*100:.1f}%")
    lines.append(f"  - `#define int long long`: {features.get('define_int_long_long', 0)*100:.1f}% (⚠️ 需要注意是否存在滥用)")
    lines.append(f"  - `ios::sync_with_stdio`: {features.get('ios_sync_with_stdio', 0)*100:.1f}% (⚠️ 如果低，说明存在大数据量 TLE 风险)")
    lines.append(f"  - `cin/cout` vs `scanf/printf`: {features.get('cin_cout', 0)*100:.1f}% vs {features.get('scanf_printf', 0)*100:.1f}%")
    lines.append(f"  - `auto` / `range-for`: {features.get('auto', 0)*100:.1f}% / {features.get('range_based_for', 0)*100:.1f}% (现代 C++ 特性)")
    
    lines.append(f"- **常用容器**: {', '.join(analysis['top_containers']) if analysis['top_containers'] else '较少使用 STL'}")
    lines.append(f"- **高频变量名**: {', '.join(analysis['top_vars'])}")

    return "\n".join(lines)
