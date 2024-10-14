from io import BytesIO
from typing import Dict, List

import requests
import timm
import torch
from loguru import logger
from PIL import Image

from ..base_model import BaseModel
from .imagenet1k_classes import IMAGENET2012_CLASSES


class TimmModel(BaseModel):
    def __init__(
        self, model_id: str, device: str = "cpu", dtype: str = "float32", **kwargs
    ):
        # device = "cuda" if device == "auto" and torch.cuda.is_available() else "cpu"

        logger.info(f"Model: {model_id}")
        logger.info(f"Device: {device}")
        logger.info(f"Dtype: {dtype}")

        dtype_map = {
            "float32": torch.float32,
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
        }
        if dtype not in dtype_map:
            raise ValueError("dtype must be one of 'float32', 'float16', or 'bfloat16'")
        dtype = dtype_map[dtype]

        super().__init__(model_id, device, dtype)
        self.load_model(**kwargs)

    def load_model(self, **kwargs):
        logger.info(f"Loading model: {self.model_id}")
        self.model = timm.create_model(self.model_id, pretrained=True, **kwargs).to(
            self.device, self.dtype
        )
        self.model = torch.compile(self.model, mode="max-autotune")
        self.model.eval()

    def preprocess(self, images: str | list[str]):
        if not isinstance(images, list):
            images = [images]

        processed_images = []
        for image_path in images:
            if not isinstance(image_path, str):
                raise ValueError("Input must be a string (local path or URL)")

            if image_path.startswith(("http://", "https://")):
                response = requests.get(image_path)
                img = Image.open(BytesIO(response.content))
            else:
                img = Image.open(image_path)

            processed_images.append(img)

        data_config = timm.data.resolve_model_data_config(self.model)
        transforms = timm.data.create_transform(**data_config, is_training=False)

        # Apply transforms to all images and stack them into a single tensor
        tensor_images = torch.stack([transforms(img) for img in processed_images])
        tensor_images = tensor_images.to(device=self.device, dtype=self.dtype)

        return tensor_images

    def infer(self, image: str, top_k: int = 5) -> List[Dict]:
        logger.info(f"Running inference on {image}")
        img = self.preprocess(image)

        with torch.inference_mode(), torch.amp.autocast(
            device_type=self.device, dtype=self.dtype
        ):
            output = self.model(img)

        topk_probabilities, topk_class_indices = torch.topk(
            output.softmax(dim=1) * 100, k=top_k
        )

        im_classes = list(IMAGENET2012_CLASSES.values())
        class_names = [im_classes[i] for i in topk_class_indices[0]]

        return [
            {"class": class_name, "id": int(class_idx), "confidence": float(prob)}
            for class_name, class_idx, prob in zip(
                class_names, topk_class_indices[0], topk_probabilities[0]
            )
        ]

    def infer_batch(self, images: List[str], top_k: int = 5) -> List[List[Dict]]:
        images = self.preprocess(images)

        with torch.inference_mode(), torch.amp.autocast(
            device_type=self.device, dtype=self.dtype
        ):
            output = self.model(images)

        topk_probabilities, topk_class_indices = torch.topk(
            output.softmax(dim=1) * 100, k=top_k
        )

        im_classes = list(IMAGENET2012_CLASSES.values())

        results = []
        for i in range(len(images)):
            class_names = [im_classes[idx] for idx in topk_class_indices[i]]
            image_results = [
                {"class": class_name, "id": int(class_idx), "confidence": float(prob)}
                for class_name, class_idx, prob in zip(
                    class_names, topk_class_indices[i], topk_probabilities[i]
                )
            ]
            results.append(image_results)

        return results
