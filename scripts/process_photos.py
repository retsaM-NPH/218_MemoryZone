import os
import sys
import json
import time
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageOps
import pillow_heif

# Register HEIF opener for Pillow
pillow_heif.register_heif_opener()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT_DIR = os.path.join(PROJECT_ROOT, 'assets', 'images')
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'assets', 'web_photos')
MANIFEST_PATH = os.path.join(PROJECT_ROOT, 'assets', 'photos_manifest.json')

VALID_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.heic'}
MAX_DIMENSION = 720
JPEG_QUALITY = 82


def extract_dominant_color(img):
    """
    Extracts an aesthetically pleasing dominant color (hex string) from an image.
    Uses median-cut quantization on a downsampled thumbnail, penalizing extreme blacks/whites
    and favoring saturated, vibrant tones.
    """
    thumb = img.resize((64, 64), Image.Resampling.BOX).convert('RGB')
    quantized = thumb.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()[:24] # 8 colors * 3 RGB channels
    colors = [(palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2]) for i in range(8)]
    counts = quantized.getcolors() or []

    best_col = colors[0] if colors else (156, 190, 201)
    max_score = -1.0

    for count, idx in counts:
        if idx >= len(colors):
            continue
        r, g, b = colors[idx]
        max_c = max(r, g, b)
        min_c = min(r, g, b)
        sat = (max_c - min_c) / max_c if max_c > 0 else 0.0
        lum = r * 0.299 + g * 0.587 + b * 0.114

        # Penalize near-black and near-white colors to preserve aesthetic vibrance
        lum_penalty = 0.25 if (lum < 35 or lum > 235) else 1.0
        score = count * (0.3 + 0.7 * sat) * lum_penalty
        if score > max_score:
            max_score = score
            best_col = (r, g, b)

    return f'#{best_col[0]:02x}{best_col[1]:02x}{best_col[2]:02x}'


def process_single_image(args):
    """
    Processes a single image file:
    - Auto-orients with EXIF
    - Handles alpha/transparency channels
    - Computes native aspect ratio
    - Scales max dimension to 720px
    - Extracts dominant color
    - Saves progressive JPEG with 82% quality
    """
    idx, filename = args
    in_path = os.path.join(INPUT_DIR, filename)
    out_name = f'photo_{idx:03d}.jpg'
    out_path = os.path.join(OUTPUT_DIR, out_name)

    try:
        with Image.open(in_path) as raw_img:
            # 1. EXIF Transpose (respect phone camera orientation)
            img = ImageOps.exif_transpose(raw_img)

            # 2. Transparency handling for PNG / RGBA
            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                bg = Image.new('RGB', img.size, (255, 255, 255))
                rgba = img.convert('RGBA')
                bg.paste(rgba, mask=rgba.split()[3])
                img = bg
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            orig_w, orig_h = img.size

            # 3. Scale longest edge to 720px
            scale = MAX_DIMENSION / max(orig_w, orig_h)
            new_w = max(1, int(round(orig_w * scale)))
            new_h = max(1, int(round(orig_h * scale)))
            aspect = round(new_w / new_h, 4)

            if (new_w, new_h) != (orig_w, orig_h):
                img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            else:
                img_resized = img

            # 4. Extract dominant color
            dom_color = extract_dominant_color(img_resized)

            # 5. Save web-optimized JPEG
            img_resized.save(out_path, 'JPEG', quality=JPEG_QUALITY, progressive=True, optimize=True)
            file_size_kb = os.path.getsize(out_path) / 1024.0

        manifest_item = {
            'id': idx,
            'url': f'assets/web_photos/{out_name}',
            'title': f'Kỷ niệm #{idx + 1}',
            'color': dom_color,
            'aspect': aspect
        }

        return {
            'success': True,
            'item': manifest_item,
            'filename': filename,
            'size_kb': file_size_kb,
            'dims': (new_w, new_h),
            'aspect': aspect
        }
    except Exception as e:
        return {
            'success': False,
            'filename': filename,
            'error': str(e)
        }


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    print('=' * 65)
    print('  MEMORYZONE 218 — BATCH IMAGE OPTIMIZATION PIPELINE (255 PHOTOS)')
    print('=' * 65)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. Scan and filter all static images
    all_files = os.listdir(INPUT_DIR)
    static_images = sorted(
        [f for f in all_files if os.path.splitext(f)[1].lower() in VALID_EXTENSIONS],
        key=lambda f: f.lower()
    )

    total = len(static_images)
    print(f'[INFO] Found {total} static image files in {INPUT_DIR}')
    if total != 255:
        print(f'[WARNING] Expected 255 images, but found {total}!')

    # 2. Multi-threaded processing
    start_time = time.time()
    work_items = [(i, fname) for i, fname in enumerate(static_images)]

    manifest = []
    total_size_kb = 0.0
    failed = []

    print(f'[INFO] Processing images using ThreadPoolExecutor...')
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(process_single_image, work_items))

    for r in results:
        if r['success']:
            manifest.append(r['item'])
            total_size_kb += r['size_kb']
        else:
            failed.append(r)

    elapsed = time.time() - start_time
    avg_size_kb = total_size_kb / len(manifest) if manifest else 0

    print(f'[SUCCESS] Processed {len(manifest)}/{total} images in {elapsed:.2f}s')
    print(f'[INFO] Total size: {total_size_kb / 1024:.2f} MB | Average per photo: {avg_size_kb:.2f} KB')

    if failed:
        print(f'[ERROR] Failed to process {len(failed)} files:')
        for f in failed:
            print(f"  - {f['filename']}: {f['error']}")
        sys.exit(1)

    # Ensure manifest is ordered strictly by id (0 to 254)
    manifest.sort(key=lambda x: x['id'])

    # 3. Write assets/photos_manifest.json
    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f'[SUCCESS] Manifest written to {MANIFEST_PATH} ({len(manifest)} items)')
    print('=' * 65)


if __name__ == '__main__':
    main()
