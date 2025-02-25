# LLM Jailbreaking



getting it running locally:
# create conda environment and activate it
conda create -n jailbreak python=3.10
conda activate jailbreak

# install dependencies
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
pip install -r requirements.txt
