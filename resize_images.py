from PIL import Image
import os

def resize_image(input_path, output_path, width, height):
    with Image.open(input_path) as img:
        # Resize using high-quality resampling
        resized_img = img.resize((width, height), Image.Resampling.LANCZOS)
        resized_img.save(output_path, "PNG")
    print(f"Resized {input_path} to {width}x{height} -> {output_path}")

# Resize banner_small.png to 1200x405
resize_image("banner_small.png", "banner_1200x405.png", 1200, 405)
# Also provide 800x270 as an option
resize_image("banner_small.png", "banner_800x270.png", 800, 270)
# Also provide 2500x843 as an option
resize_image("banner_small.png", "banner_2500x843.png", 2500, 843)
