import cv2
import numpy as np

def upscale_image(image_path, scale_factor):
    # Read the input image
    img = cv2.imread(image_path)

    # Calculate the new dimensions after scaling
    height, width, _ = img.shape
    new_height = int(height * scale_factor)
    new_width = int(width * scale_factor)

    # Create a new image with the scaled dimensions
    new_img = np.zeros((new_height, new_width, 3), dtype=np.uint8)

    # Upscale the image using bicubic interpolation
    for i in range(new_height):
        for j in range(new_width):
            x = int((i / scale_factor) % height)
            y = int((j / scale_factor) % width)
            new_img[i, j] = img[x, y]

    # Save the upscaled image
    cv2.imwrite('upscaled_image.jpg', new_img)

# Example usage:
image_path = 'input_image.jpg'
scale_factor = 4.0
upscale_image(image_path, scale_factor)