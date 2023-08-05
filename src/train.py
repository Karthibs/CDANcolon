import os
import time
import random
import sys

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
from torchvision import transforms
from torchmetrics import PeakSignalNoiseRatio
from torchmetrics import StructuralSimilarityIndexMeasure
import torch.nn.functional as F
import torchvision.models as models

from PIL import Image
from tqdm import tqdm
import numpy as np
import cv2
from decouple import config

from utils.save_model import save_model
from utils.save_output_images import save_output_images
from dataset import LLIDataset
from models.model import AutoEncoder
from models.res_cbam import LLIE
from models.res_bam import ResBAM
from models.enhanced_model import EnhancedAutoEncoder
from models.pixel_shuffle import Pixel
from models.mix import Mix
from models.vae import MixVAE


def train(model, optimizer, criterion, n_epoch,
          data_loaders: dict, device, lr_scheduler=None
          ):
    vgg = models.vgg19(
        weights=models.VGG19_Weights.IMAGENET1K_V1).features[:16].to(device)
    perceptual_loss_weight = 0.5  # Adjust the weight as desired
    train_losses = np.zeros(n_epoch)
    val_losses = np.zeros(n_epoch)
    best_psnr = 0.0

    model.to(device)
    psnr = PeakSignalNoiseRatio().to(device)
    ssim = StructuralSimilarityIndexMeasure().to(device)

    since = time.time()

    for epoch in range(n_epoch):
        train_loss = 0.0
        train_psnr = 0.0
        train_ssim = 0.0
        model.train()
        for inputs, targets in tqdm(data_loaders['train'], desc=f'Training... Epoch: {epoch + 1}/{EPOCHS}'):
            inputs, targets = inputs.to(device), targets.to(device)

            optimizer.zero_grad()

            outputs = model(inputs)
            loss = criterion(outputs, targets)
            perceptual_loss = perceptual_loss_weight * \
                F.mse_loss(vgg(outputs), vgg(targets))
            loss = loss + perceptual_loss
            train_loss += loss.item()
            train_psnr += psnr(outputs, targets)
            train_ssim += ssim(outputs, targets)

            loss.backward()
            optimizer.step()

        train_loss = train_loss / len(data_loaders['train'])
        train_psnr = train_psnr / len(data_loaders['train'])
        train_ssim = train_ssim / len(data_loaders['train'])

        with torch.no_grad():
            val_loss = 0.0
            val_psnr = 0.0
            val_ssim = 0.0
            model.eval()
            for inputs, targets in tqdm(data_loaders['validation'], desc=f'Validating... Epoch: {epoch + 1}/{EPOCHS}'):
                inputs, targets = inputs.to(device), targets.to(device)

                optimizer.zero_grad()

                outputs = model(inputs)
                loss = criterion(outputs, targets)
                perceptual_loss = perceptual_loss_weight * \
                    F.mse_loss(vgg(outputs), vgg(targets))
                loss = loss + perceptual_loss
                val_loss += loss.item()
                val_psnr += psnr(outputs, targets)
                val_ssim += ssim(outputs, targets)

                # Save output images every 20 epoch
                if (epoch + 1) % 20 == 0:
                    save_output_images(outputs, SAVE_DIR_ROOT, epoch, MODEL_NAME, device)

            val_loss = val_loss / len(data_loaders['validation'])
            val_psnr = val_psnr / len(data_loaders['validation'])
            val_ssim = val_ssim / len(data_loaders['validation'])

            if val_psnr > best_psnr:
                save_model(model, optimizer, val_loss, val_psnr,
                           val_ssim, epoch, SAVE_DIR_ROOT, MODEL_NAME, device)
                save_output_images(outputs, SAVE_DIR_ROOT, epoch, MODEL_NAME, device, True)

        # save epoch losses
        train_losses[epoch] = train_loss
        val_losses[epoch] = val_loss

        print(f"Epoch [{epoch+1}/{n_epoch}]:")
        print(
            f"Train Loss: {train_loss:.4f}, Train PSNR: {train_psnr:.4f}, Train SSIM: {train_ssim:.4f}")
        print(
            f"Validation Loss: {val_loss:.4f}, Validation PSNR: {val_psnr:.4f}, Validation SSIM: {val_ssim:.4f}")
        print('-'*20)

    time_elapsed = time.time() - since
    print('Training completed in {:.0f}m {:.0f}s'.format(
        time_elapsed // 60, time_elapsed % 60))


if __name__ == '__main__':
    seed_value = 42
    random.seed(seed_value)
    np.random.seed(seed_value)
    torch.manual_seed(seed_value)
    torch.cuda.manual_seed(seed_value)
    torch.cuda.manual_seed_all(seed_value)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

    INPUT_SIZE = 128
    DATASET_DIR_ROOT = config('DATASET_DIR_ROOT')
    SAVE_DIR_ROOT = config('SAVE_DIR_ROOT')
    MODEL_NAME = 'SimpleCNN'
    BATCH_SIZE = 32
    EPOCHS = 200

    device = 'cpu'
    if torch.cuda.is_available():
        device = 'cuda:0'
    elif torch.backends.mps.is_available():
        device = 'mps'

    train_transforms = transforms.Compose([
        transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
        transforms.ToTensor(),
    ])

    test_transforms = transforms.Compose([
        transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
        transforms.ToTensor(),
    ])

    train_dataset = LLIDataset(
        os.path.join(DATASET_DIR_ROOT, 'train', 'low'),
        os.path.join(DATASET_DIR_ROOT, 'train', 'high'),
        train_transforms,
        train_transforms
    )
    test_dataset = LLIDataset(
        os.path.join(DATASET_DIR_ROOT, 'test', 'low'),
        os.path.join(DATASET_DIR_ROOT, 'test', 'high'),
        test_transforms,
        test_transforms
    )

    data_loaders = {
        'train': DataLoader(
            train_dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=1
        ),
        'validation': DataLoader(
            test_dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=1
        )
    }

    model = Mix().to(device)
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=1e-3)

    train(model, optimizer, criterion, EPOCHS, data_loaders, device)
