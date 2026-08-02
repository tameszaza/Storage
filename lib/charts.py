import math
import os
import uuid
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.patheffects as path_effects
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Wedge

from lib.storage import format_bytes, safe_upload_path


SYSTEM_DIRECTORY_NAME = ".tamestorage_system"
SUNBURST_MAX_RINGS = 4
SUNBURST_MAX_CHILDREN = 12
SUNBURST_MIN_CHILD_SHARE = 0.004


def analyze_directory_space(directory: str) -> tuple[dict, dict]:
    directory_data = {}
    file_type_data = {}
    for root, directories, files in os.walk(directory):
        directories[:] = [name for name in directories if name != SYSTEM_DIRECTORY_NAME]
        total_size = 0
        for filename in files:
            file_path = os.path.join(root, filename)
            if os.path.islink(file_path):
                continue
            try:
                size = os.path.getsize(file_path)
            except OSError:
                continue
            total_size += size
            ext = os.path.splitext(filename)[1].lower() or "No extension"
            file_type_data[ext] = file_type_data.get(ext, 0) + size
        directory_data[root] = total_size
    return directory_data, file_type_data


def chart_rows(data: dict, is_file_type: bool = False) -> tuple[list[str], list[int], list[tuple]]:
    total_size = sum(data.values())
    if total_size <= 0:
        return ["Empty"], [1], [("Empty", 0, 100)]

    threshold = total_size * 0.01
    other_size = 0
    labels = []
    sizes = []
    info = []

    for key, size in data.items():
        if size < threshold:
            other_size += size
            continue
        label_name = key if is_file_type else os.path.basename(key) or "Root"
        label = f"{label_name} ({format_bytes(size)})"
        percentage = (size / total_size) * 100
        labels.append(label)
        sizes.append(size)
        info.append((label, round(size / (1024 ** 2), 2), round(percentage, 1)))

    if other_size > 0:
        label = f"Other ({format_bytes(other_size)})"
        percentage = (other_size / total_size) * 100
        labels.append(label)
        sizes.append(other_size)
        info.append((label, round(other_size / (1024 ** 2), 2), round(percentage, 1)))

    return labels or ["Empty"], sizes or [1], info or [("Empty", 0, 100)]


def generate_pie_chart(data: dict, is_file_type: bool = False) -> tuple[str, list[tuple]]:
    labels, sizes, info = chart_rows(data, is_file_type)
    fig, ax = plt.subplots(figsize=(9, 9))
    wedges, texts, autotexts = ax.pie(
        sizes,
        labels=labels,
        startangle=90,
        colors=plt.cm.Paired.colors,
        wedgeprops={"edgecolor": "white", "linewidth": 1.5},
        autopct="%1.1f%%",
        textprops={"fontsize": 10, "color": "black"},
    )
    for text in texts + autotexts:
        text.set_path_effects([
            path_effects.withStroke(linewidth=3, foreground="white", alpha=0.8),
            path_effects.Normal(),
        ])
    ax.axis("equal")
    chart_path = _new_chart_path(".png")
    plt.savefig(chart_path, transparent=True, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return chart_path, info


def _new_chart_path(extension: str) -> str:
    charts_dir = safe_upload_path("Admin", "charts")
    os.makedirs(charts_dir, exist_ok=True)
    return os.path.join(charts_dir, f"{uuid.uuid4()}{extension}")


def _directory_hierarchy(directory: str, display_name: str) -> tuple[dict, dict]:
    root_path = os.path.abspath(directory)
    records: dict[str, dict] = {}
    file_type_data: dict[str, int] = {}

    for current_root, directories, files in os.walk(root_path, topdown=True):
        directories[:] = sorted(name for name in directories if name != SYSTEM_DIRECTORY_NAME)
        direct_size = 0
        direct_file_count = 0
        for filename in files:
            file_path = os.path.join(current_root, filename)
            if os.path.islink(file_path):
                continue
            try:
                file_size = os.path.getsize(file_path)
            except OSError:
                continue
            direct_size += file_size
            direct_file_count += 1
            extension = os.path.splitext(filename)[1].lower() or "No extension"
            file_type_data[extension] = file_type_data.get(extension, 0) + file_size
        records[current_root] = {
            "directories": list(directories),
            "direct_size": direct_size,
            "direct_file_count": direct_file_count,
        }

    nodes: dict[str, dict] = {}
    ordered_paths = sorted(records, key=lambda value: len(Path(value).parts), reverse=True)
    for current_root in ordered_paths:
        record = records[current_root]
        children = []
        for directory_name in record["directories"]:
            child_path = os.path.join(current_root, directory_name)
            child_node = nodes.get(child_path)
            if child_node and child_node["size"] > 0:
                children.append(child_node)

        direct_size = record["direct_size"]
        relative = os.path.relpath(current_root, root_path)
        node_name = display_name if relative == "." else os.path.basename(current_root)
        nodes[current_root] = {
            "name": node_name or "Root",
            "path": normalize_chart_path(relative),
            "size": direct_size + sum(child["size"] for child in children),
            "direct_size": direct_size,
            "file_count": record["direct_file_count"] + sum(child.get("file_count", 0) for child in children),
            "direct_file_count": record["direct_file_count"],
            "children": sorted(children, key=lambda child: child["size"], reverse=True),
            "kind": "folder",
        }

    root_node = nodes.get(root_path, {
        "name": display_name or "Root",
        "path": "",
        "size": 0,
        "direct_size": 0,
        "file_count": 0,
        "direct_file_count": 0,
        "children": [],
        "kind": "folder",
    })
    return root_node, file_type_data


def build_storage_analysis(directory: str, display_name: str) -> tuple[dict, dict]:
    """Return the folder hierarchy and file-type totals for interactive charts."""
    return _directory_hierarchy(directory, display_name)


def normalize_chart_path(path: str) -> str:
    return "" if path in {"", "."} else str(path).replace("\\", "/")


def _tree_depth(node: dict, remaining: int = SUNBURST_MAX_RINGS) -> int:
    if remaining <= 0:
        return 0
    children = [child for child in node.get("children", []) if child.get("size", 0) > 0]
    if not children:
        return 0
    return 1 + max(_tree_depth(child, remaining - 1) for child in children)


def _compact_children(node: dict) -> list[dict]:
    total = max(0, int(node.get("size", 0)))
    children = [child for child in node.get("children", []) if child.get("size", 0) > 0]
    children.sort(key=lambda child: child["size"], reverse=True)
    if not children or total <= 0:
        return []

    kept = []
    grouped = []
    for index, child in enumerate(children):
        share = child["size"] / total
        if index < SUNBURST_MAX_CHILDREN and (share >= SUNBURST_MIN_CHILD_SHARE or index < 4):
            kept.append(child)
        else:
            grouped.append(child)

    if grouped:
        kept.append({
            "name": "Other",
            "path": node.get("path", ""),
            "size": sum(child["size"] for child in grouped),
            "file_count": sum(child.get("file_count", 0) for child in grouped),
            "children": [],
            "kind": "other",
        })
    return kept


def _lighten(color, amount: float):
    red, green, blue = mcolors.to_rgb(color)
    amount = min(max(amount, 0), 0.72)
    return (
        red + (1 - red) * amount,
        green + (1 - green) * amount,
        blue + (1 - blue) * amount,
    )


def _arc_label(name: str, span: float) -> str:
    max_chars = 18 if span >= 28 else 12
    if len(name) <= max_chars:
        return name
    return name[: max(4, max_chars - 1)].rstrip() + "…"


def _draw_sunburst_children(
    ax,
    node: dict,
    start_angle: float,
    span: float,
    depth: int,
    ring_width: float,
    center_radius: float,
    base_color,
    max_depth: int,
):
    if depth > max_depth or node.get("size", 0) <= 0:
        return

    children = _compact_children(node)
    if not children:
        return

    cursor = start_angle
    parent_size = node["size"]
    for child_index, child in enumerate(children):
        child_span = span * (child["size"] / parent_size)
        if child_span <= 0:
            continue

        if depth == 1:
            palette = plt.cm.tab20.colors
            color = palette[child_index % len(palette)]
            descendant_base = color
        else:
            color = _lighten(base_color, 0.13 * (depth - 1))
            descendant_base = base_color

        inner_radius = center_radius + (depth - 1) * ring_width
        outer_radius = center_radius + depth * ring_width
        wedge = Wedge(
            (0, 0),
            outer_radius,
            cursor,
            cursor + child_span,
            width=ring_width,
            facecolor=color,
            edgecolor="white",
            linewidth=1.35,
        )
        ax.add_patch(wedge)

        if child_span >= 7:
            middle_angle = cursor + child_span / 2
            radians = middle_angle * 3.141592653589793 / 180
            label_radius = inner_radius + ring_width * 0.56
            rotation = middle_angle - 90
            if 90 < (middle_angle % 360) < 270:
                rotation += 180
            text = ax.text(
                label_radius * math.cos(radians),
                label_radius * math.sin(radians),
                _arc_label(child["name"], child_span),
                ha="center",
                va="center",
                rotation=rotation,
                rotation_mode="anchor",
                fontsize=max(6.5, 9.4 - depth * 0.65),
                color="#111827",
                clip_on=True,
            )
            text.set_path_effects([
                path_effects.withStroke(linewidth=2.4, foreground="white", alpha=0.82),
                path_effects.Normal(),
            ])

        _draw_sunburst_children(
            ax,
            child,
            cursor,
            child_span,
            depth + 1,
            ring_width,
            center_radius,
            descendant_base,
            max_depth,
        )
        cursor += child_span


def generate_directory_sunburst(directory: str, display_name: str) -> tuple[str, list[tuple], dict]:
    root, file_type_data = _directory_hierarchy(directory, display_name)
    total_size = root.get("size", 0)
    max_depth = min(SUNBURST_MAX_RINGS, max(1, _tree_depth(root)))
    center_radius = 0.25
    ring_width = (1.0 - center_radius) / max_depth

    fig, ax = plt.subplots(figsize=(9.5, 9.5))
    ax.set_aspect("equal")
    ax.set_xlim(-1.08, 1.08)
    ax.set_ylim(-1.08, 1.08)
    ax.axis("off")

    if total_size > 0:
        _draw_sunburst_children(
            ax,
            root,
            start_angle=90,
            span=360,
            depth=1,
            ring_width=ring_width,
            center_radius=center_radius,
            base_color=plt.cm.tab20.colors[0],
            max_depth=max_depth,
        )
    else:
        ax.add_patch(Wedge((0, 0), 1.0, 0, 360, width=1.0 - center_radius, facecolor="#e5e7eb", edgecolor="white"))

    ax.add_patch(Circle((0, 0), center_radius, facecolor="#ffffff", edgecolor="#d1d5db", linewidth=1.5))
    center_name = root.get("name") or "Root"
    if len(center_name) > 22:
        center_name = center_name[:21] + "…"
    center_text = ax.text(0, 0.035, center_name, ha="center", va="center", fontsize=12, fontweight="bold", color="#111827")
    center_text.set_path_effects([path_effects.withStroke(linewidth=2.2, foreground="white"), path_effects.Normal()])
    ax.text(0, -0.045, format_bytes(total_size), ha="center", va="center", fontsize=9.5, color="#64748b")

    info = []
    for child in _compact_children(root):
        percentage = (child["size"] / total_size * 100) if total_size else 0
        info.append((f"{child['name']} ({format_bytes(child['size'])})", round(child["size"] / (1024 ** 2), 2), round(percentage, 1)))
    if not info:
        info = [("Empty", 0, 100)]

    chart_path = _new_chart_path(".svg")
    plt.savefig(chart_path, transparent=True, bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)
    return chart_path, info, {
        "file_count": root.get("file_count", 0),
        "total_size": total_size,
        "file_type_data": file_type_data,
    }


def clear_charts() -> None:
    charts_dir = safe_upload_path("Admin", "charts")
    os.makedirs(charts_dir, exist_ok=True)
    for filename in os.listdir(charts_dir):
        file_path = os.path.join(charts_dir, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)
