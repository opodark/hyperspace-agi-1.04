# Training LoRA SDXL/Pony

Questa pipeline addestra una LoRA di identità compatibile con il checkpoint
CyberRealistic Pony usato dal bridge Mac. Non addestra pose: quelle restano a
ControlNet/OpenPose.

## Dataset

Mettere 25–50 immagini curate in `data/training/anna/`. Ogni immagine deve avere
una caption omonima, per esempio `001.jpg` + `001.txt`. La prima parola della
caption deve essere il token univoco dell'identità, ad esempio `hsanna`:

```text
hsanna, adult woman, full-body portrait, standing outdoors, red dress
```

Variare primi piani, mezzi busti, figure intere, sfondi, vestiti, illuminazioni e
rapporti d'aspetto. Non ripetere la stessa posa o lo stesso abito nella maggior
parte delle immagini, altrimenti finiranno dentro l'identità.

Usare soltanto immagini proprie o autorizzate. Non mescolare persone diverse.

## Comandi

```bash
integrations/training/install-kohya.sh
integrations/training/train-kohya.sh
```

Il validatore blocca dataset piccoli, caption mancanti, file illeggibili e
risoluzioni sotto 512 px. Il profilo usa LoRA rank 32/alpha 16, bucket SDXL,
latent cache, gradient checkpointing e batch 1. Le immagini di verifica per
epoca usano `sample_prompts.txt` (token `hsanna`): modificale per controllare
l'identità con pose e luci tue.

Il training SDXL è consigliato su CUDA con almeno 12–16 GB; 24 GB offre più
margine. Il Mac può preparare e validare il dataset, ma non è il nodo consigliato
per il training definitivo.

Al termine copiare il `.safetensors` in:

```text
ComfyUI-Shared/models/loras/HyperSpace/anna_identity_sdxl.safetensors
```

e configurare:

```dotenv
SDXL_LORA_NAME=HyperSpace/anna_identity_sdxl.safetensors
SDXL_LORA_STRENGTH=0.8
```

Il workflow aggiunge `LoraLoader` soltanto quando il nome non è vuoto.
