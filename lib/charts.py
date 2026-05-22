import os
import uuid
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as path_effects
from flask import current_app
from lib.storage import safe_upload_path


def analyze_directory_space(directory: str) -> tuple[dict, dict]:
    directory_data = {}
    file_type_data = {}
    for root, _, files in os.walk(directory):
        total_size = 0
        for filename in files:
            file_path = os.path.join(root, filename)
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
        label = f"{label_name} ({size // (1024 ** 2)} MB)"
        percentage = (size / total_size) * 100
        labels.append(label)
        sizes.append(size)
        info.append((label, size // (1024 ** 2), round(percentage, 1)))

    if other_size > 0:
        label = f"Other ({other_size // (1024 ** 2)} MB)"
        percentage = (other_size / total_size) * 100
        labels.append(label)
        sizes.append(other_size)
        info.append((label, other_size // (1024 ** 2), round(percentage, 1)))

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
    charts_dir = safe_upload_path("Admin", "charts")
    os.makedirs(charts_dir, exist_ok=True)
    chart_filename = f"{uuid.uuid4()}.png"
    chart_path = os.path.join(charts_dir, chart_filename)
    plt.savefig(chart_path, transparent=True, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return chart_path, info


def clear_charts() -> None:
    charts_dir = safe_upload_path("Admin", "charts")
    os.makedirs(charts_dir, exist_ok=True)
    for filename in os.listdir(charts_dir):
        file_path = os.path.join(charts_dir, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)
