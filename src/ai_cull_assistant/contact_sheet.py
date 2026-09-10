from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import ceil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .models import PhotoAsset
from .subject import features, face_crop

PAGE_BG = "white"
TEXT_COLOR = "black"
GROUP_BG = (236, 236, 236)
REJECT_BG = (250, 232, 232)
# Four columns -> ~2024 px wide, close to the 2048 px target for chat upload.
THUMB_BOX = (456, 380)
CELL_W = 490
CELL_H = 452
GROUP_HEADER_H = 48
PAGE_HEADER_H = 78
MARGIN = 32
MAX_PAGE_HEIGHT = 2800


@dataclass(slots=True)
class ContactSheetSet:
    main_pages: list[Path]
    rejected_pages: list[Path]


def paginate_by_group(assets: list[PhotoAsset], photos_per_page: int = 24) -> list[list[PhotoAsset]]:
    """Paginate while keeping normal-sized groups intact whenever possible."""
    if photos_per_page < 1:
        raise ValueError("photos_per_page must be >= 1")
    if not assets:
        return []

    groups: list[list[PhotoAsset]] = []
    current_group_id = None
    for asset in assets:
        if current_group_id != asset.group_id:
            groups.append([])
            current_group_id = asset.group_id
        groups[-1].append(asset)

    pages: list[list[PhotoAsset]] = []
    current: list[PhotoAsset] = []
    for group in groups:
        if len(group) > photos_per_page:
            if current:
                pages.append(current)
                current = []
            for start in range(0, len(group), photos_per_page):
                pages.append(group[start:start + photos_per_page])
            continue
        if current and len(current) + len(group) > photos_per_page:
            pages.append(current)
            current = []
        current.extend(group)
    if current:
        pages.append(current)
    return pages


def page_filename(assets: list[PhotoAsset], page_index: int) -> str:
    if not assets:
        return f"sheet_{page_index:03d}_empty.jpg"
    group_ids = [asset.group_id for asset in assets]
    return (
        f"sheet_{page_index:03d}_"
        f"G{min(group_ids):03d}-G{max(group_ids):03d}_"
        f"{assets[0].stem}-{assets[-1].stem}.jpg"
    )



def estimate_page_height(assets: list[PhotoAsset], columns: int = 4) -> int:
    if not assets:
        return MARGIN * 2 + PAGE_HEADER_H
    height = MARGIN * 2 + PAGE_HEADER_H
    for _, group_assets in _chunk_groups(assets):
        height += GROUP_HEADER_H + ceil(len(group_assets) / columns) * CELL_H
    return height


def paginate_for_layout(
    assets: list[PhotoAsset],
    photos_per_page: int = 20,
    columns: int = 4,
    max_page_height: int = MAX_PAGE_HEIGHT,
) -> list[list[PhotoAsset]]:
    """Paginate by both photo count and rendered page height."""
    if photos_per_page < 1:
        raise ValueError("photos_per_page must be >= 1")
    if columns < 1:
        raise ValueError("columns must be >= 1")
    if not assets:
        return []

    groups = _chunk_groups(assets)
    usable = max_page_height - (MARGIN * 2 + PAGE_HEADER_H + GROUP_HEADER_H)
    max_rows_for_single_group = max(1, usable // CELL_H)
    max_single_group_photos = max(1, min(photos_per_page, max_rows_for_single_group * columns))

    pages: list[list[PhotoAsset]] = []
    current: list[PhotoAsset] = []
    for _, group in groups:
        if len(group) > max_single_group_photos:
            if current:
                pages.append(current)
                current = []
            for start in range(0, len(group), max_single_group_photos):
                pages.append(group[start:start + max_single_group_photos])
            continue

        candidate = current + group
        would_overflow = (
            len(candidate) > photos_per_page
            or estimate_page_height(candidate, columns) > max_page_height
        )
        if current and would_overflow:
            pages.append(current)
            current = list(group)
        else:
            current = candidate
    if current:
        pages.append(current)
    return pages

def generate_contact_sheet_sets(
    assets: list[PhotoAsset],
    out_dir: str | Path,
    photos_per_page: int = 20,
    columns: int = 4,
) -> ContactSheetSet:
    root = Path(out_dir)
    main_dir = root / "main"
    rejected_dir = root / "rejected_review"
    main_assets = [asset for asset in assets if not asset.auto_rejected]
    rejected_assets = [asset for asset in assets if asset.auto_rejected]

    main_pages = generate_contact_sheets(
        main_assets,
        main_dir,
        photos_per_page=photos_per_page,
        columns=columns,
        review_mode=False,
    )
    rejected_pages = generate_contact_sheets(
        rejected_assets,
        rejected_dir,
        photos_per_page=photos_per_page,
        columns=columns,
        review_mode=True,
    )
    # Remove legacy root pages from older versions so users do not upload stale sheets.
    for pattern in ("contact_sheet_*.jpg", "sheet_*.jpg"):
        for stale in root.glob(pattern):
            try:
                stale.unlink()
            except OSError:
                pass
    return ContactSheetSet(main_pages=main_pages, rejected_pages=rejected_pages)


def generate_contact_sheets(
    assets: list[PhotoAsset],
    out_dir: str | Path,
    photos_per_page: int = 20,
    columns: int = 4,
    *,
    review_mode: bool = False,
) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for pattern in ("contact_sheet_*.jpg", "sheet_*.jpg"):
        for stale in out.glob(pattern):
            try:
                stale.unlink()
            except OSError:
                pass

    pages_data = paginate_for_layout(assets, photos_per_page=photos_per_page, columns=columns)
    title_font = _load_font(30)
    group_font = _load_font(23)
    filename_font = _load_font(25)
    small_font = _load_font(18)
    pages: list[Path] = []
    total_pages = len(pages_data)

    group_total_counts = Counter(asset.group_id for asset in assets)
    group_page_counts: dict[int, int] = Counter()
    for page in pages_data:
        for group_id, _ in _chunk_groups(page):
            group_page_counts[group_id] += 1
    group_page_seen: dict[int, int] = defaultdict(int)

    for page_index, chunk in enumerate(pages_data, start=1):
        group_chunks = _chunk_groups(chunk)
        page_w = MARGIN * 2 + columns * CELL_W
        page_h = MARGIN * 2 + PAGE_HEADER_H
        for _, group_assets in group_chunks:
            page_h += GROUP_HEADER_H + ceil(len(group_assets) / columns) * CELL_H

        canvas = Image.new("RGB", (page_w, page_h), PAGE_BG)
        draw = ImageDraw.Draw(canvas)
        page_title = "Rejected Review" if review_mode else "AI Photo Culling Sheet"
        draw.text((MARGIN, MARGIN), f"{page_title}  {page_index}/{total_pages}", fill=TEXT_COLOR, font=title_font)
        sub = f"Photos: {len(chunk)}   Groups: {len(group_chunks)}"
        if review_mode:
            sub += "   Auto-rejected: obvious subject blur / severe shake only"
        draw.text((MARGIN, MARGIN + 42), sub, fill=TEXT_COLOR, font=small_font)

        y_cursor = MARGIN + PAGE_HEADER_H
        for group_id, group_assets in group_chunks:
            group_page_seen[group_id] += 1
            part_idx = group_page_seen[group_id]
            part_total = group_page_counts[group_id]
            total_count = group_total_counts[group_id]
            segment_count = len(group_assets)
            header_top = y_cursor
            fill = REJECT_BG if review_mode else GROUP_BG
            draw.rectangle([MARGIN, header_top, page_w - MARGIN, header_top + GROUP_HEADER_H - 4], fill=fill)
            if part_total > 1:
                group_label = (
                    f"G{group_id:03d}   ·   {segment_count}/{total_count} photos"
                    f"   ·   part {part_idx}/{part_total}"
                )
            else:
                group_label = f"G{group_id:03d}   ·   {total_count} photos"
            draw.text((MARGIN + 12, header_top + 9), group_label, fill=TEXT_COLOR, font=group_font)
            y_cursor += GROUP_HEADER_H
            # Compute group-local offset so #01/#02 remain stable even across page splits.
            group_assets_all = [a for a in assets if a.group_id == group_id]
            index_map = {id(a): i + 1 for i, a in enumerate(group_assets_all)}
            for idx, asset in enumerate(group_assets):
                row = idx // columns
                col = idx % columns
                x0 = MARGIN + col * CELL_W
                y0 = y_cursor + row * CELL_H
                _draw_cell(
                    canvas,
                    draw,
                    asset,
                    x0,
                    y0,
                    index_map[id(asset)],
                    filename_font,
                    small_font,
                    review_mode=review_mode,
                )
            y_cursor += ceil(len(group_assets) / columns) * CELL_H

        page_path = out / page_filename(chunk, page_index)
        canvas.save(page_path, "JPEG", quality=93, optimize=True)
        pages.append(page_path)
    return pages


def _chunk_groups(chunk: list[PhotoAsset]) -> list[tuple[int, list[PhotoAsset]]]:
    result: list[tuple[int, list[PhotoAsset]]] = []
    current_id: int | None = None
    current_assets: list[PhotoAsset] = []
    for asset in chunk:
        if current_id is None or asset.group_id != current_id:
            if current_assets:
                result.append((current_id, current_assets))  # type: ignore[arg-type]
            current_id = asset.group_id
            current_assets = [asset]
        else:
            current_assets.append(asset)
    if current_assets:
        result.append((current_id, current_assets))  # type: ignore[arg-type]
    return result


def _draw_cell(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    asset: PhotoAsset,
    x0: int,
    y0: int,
    group_index: int,
    filename_font: ImageFont.ImageFont,
    small_font: ImageFont.ImageFont,
    *,
    review_mode: bool = False,
) -> None:
    border = (190, 90, 90) if review_mode else (180, 180, 180)
    draw.rectangle([x0, y0, x0 + CELL_W - 8, y0 + CELL_H - 8], outline=border, width=1)

    bottom = y0 + THUMB_BOX[1]
    if asset.preview_path and Path(asset.preview_path).exists():
        with Image.open(asset.preview_path) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            subject = features(Path(asset.preview_path))
            face = subject.face if subject else None
            photo_box = (330, THUMB_BOX[1]) if face else THUMB_BOX
            thumb = ImageOps.contain(img, photo_box)
            paste_x = x0 + 10 + (photo_box[0] - thumb.width) // 2
            paste_y = y0 + 8
            canvas.paste(thumb, (paste_x, paste_y))
            if face:
                crop = ImageOps.contain(face_crop(img, face, subject.head), (124, 150))
                inset_x = x0 + 350
                inset_y = y0 + 30
                draw.text((inset_x, y0 + 7), "FACE", fill=TEXT_COLOR, font=small_font)
                canvas.paste(crop, (inset_x + (124 - crop.width) // 2, inset_y))
                draw.rectangle([inset_x - 2, inset_y - 2, inset_x + 126, inset_y + crop.height + 2], outline=(100, 100, 100), width=2)
            bottom = paste_y + thumb.height
    else:
        draw.rectangle([x0 + 10, y0 + 10, x0 + CELL_W - 18, y0 + THUMB_BOX[1]], outline=(180, 0, 0))
        draw.text((x0 + 20, y0 + 20), "Preview unavailable", fill=(180, 0, 0), font=small_font)

    text_y = max(y0 + THUMB_BOX[1] + 10, bottom + 8)
    label = f"{asset.stem} · #{group_index:02d}"
    label_size = 25
    while draw.textbbox((0, 0), label, font=filename_font)[2] > CELL_W - 28 and label_size > 10:
        label_size -= 1
        filename_font = _load_font(label_size)
    draw.text((x0 + 12, text_y), label, fill=TEXT_COLOR, font=filename_font)
    if review_mode:
        meta = "AUTO REJECT"
        if asset.focus_score is not None:
            meta += f" · focus: {asset.focus_score:.2f}"
        draw.text((x0 + 12, text_y + 29), meta, fill=TEXT_COLOR, font=small_font)


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default()
