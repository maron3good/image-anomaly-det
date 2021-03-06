from dlcliche.utils import *
from dlcliche.torch_utils import IntraBatchMixup
import torch
from torch import nn
import time
import copy


def torch_flooding(loss, b):
    """Flooding function."""
    return (loss - b).abs() + b


def _get_model_wts(det):
    return {'model': det.model.state_dict(),
            'metric_fc': det.metric_fc.state_dict()}


def _set_model_wts(det, wts):
    det.model.load_state_dict(wts['model'])
    det.metric_fc.load_state_dict(wts['metric_fc'])


def train_model(det, criterion, optimizer, scheduler,
                dataloaders, num_epochs, flooding_b, device):
    since = time.time()

    mixup_alpha = 0.0

    best_model_wts = _get_model_wts(det)
    best_acc = 0.0
    best_loss = 1e10
    
    dataset_sizes = {phase: len(dataloaders[phase].dataset)
                     for phase in ['train', 'val']}

    batch_tfm = IntraBatchMixup(criterion, alpha=mixup_alpha) if mixup_alpha > 0.0 else None

    for epoch in range(num_epochs):
        prm_grps = optimizer.param_groups[0]
        momentum_str = f' m:{prm_grps["momentum"]:.05f}' if 'momentum' in prm_grps else ''
        print(f'Epoch {epoch}/{num_epochs} lr:{prm_grps["lr"]:.07f}{momentum_str}', end='')

        # Each epoch has a training and validation phase
        for phase in ['train', 'val']:
            if phase == 'train':
                det.model.train()  # Set model to training mode
                det.metric_fc.train()
            else:
                det.model.eval()   # Set model to evaluate mode
                det.metric_fc.eval()

            running_loss = 0.0
            running_corrects = 0

            # Make the albumentations do deterministically
            dataloaders[phase].dataset.set_epoch(epoch)

            # Iterate over data.
            for inputs, labels in dataloaders[phase]:
                inputs, labels = inputs.to(device), labels.to(device)
                if batch_tfm:
                    inputs, labels = batch_tfm.transform(inputs, labels, train=(phase == 'train'))

                # zero the parameter gradients
                optimizer.zero_grad()

                # forwaself.weightsdetrd
                # track history if only in train
                with torch.set_grad_enabled(phase == 'train'):
                    if batch_tfm:
                        outputs = det.clf_forward(inputs, labels[0])
                        outputs2 = det.clf_forward(inputs, labels[1])
                        loss = batch_tfm.criterion(outputs, labels, outputs2=outputs2)
                    else:
                        outputs = det.clf_forward(inputs, labels)
                        loss = criterion(outputs, labels)
                    _, preds = torch.max(outputs, 1)

                    # flooding
                    if flooding_b > 0.0 and phase == 'train':
                        loss = torch_flooding(loss, flooding_b)

                    # backward + optimize only if in training phase
                    if phase == 'train':
                        loss.backward()
                        optimizer.step()

                # statistics
                running_loss += loss.item() * inputs.size(0)
                running_corrects += torch.sum(preds == (labels[0] if batch_tfm else labels).data)

            if phase == 'train':
                scheduler.step()

            epoch_loss = running_loss / dataset_sizes[phase]
            epoch_acc = running_corrects.double() / dataset_sizes[phase]

            print(f'  {phase} loss: {epoch_loss:.4f} acc: {epoch_acc:.4f}', end='')

            # deep copy the model
            if phase == 'val' and epoch_loss < best_loss: #epoch_acc > best_acc:
                best_acc = epoch_acc
                best_loss = epoch_loss
                best_model_wts = _get_model_wts(det)
                print(f'\nUpdate: Best val acc/loss: {best_acc:4f}/{best_loss:4f}', end='')
        print()

    time_elapsed = time.time() - since
    print(f'Training complete in {time_elapsed // 60:.0f}m {time_elapsed % 60:.0f}s')
    print(f'Best val Acc/Loss: {best_acc:4f}/{best_loss:4f}')

    # load best model weights
    last_wts = _get_model_wts(det)
    _set_model_wts(det, best_model_wts)

    return {
        'best_acc': best_acc,
        'best_loss': best_loss,
        'last_weights': last_wts,
        'best_weights': best_model_wts
    }

