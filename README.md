conda create -n cloudagent python=3.11
conda activate cloudagent
pip install -r requirements.txt

docker build -t cloudops-agent
docker run --rm --name cloudops\
  -p 8001:8000 \
  --env-file .env \
  -e PORT=8000 \
  -e WORKSPACE_ROOT=/workspace \
  -v "$(pwd)":/workspace \

Need to create .env and put your GEMINI_API_KEY=XXX.