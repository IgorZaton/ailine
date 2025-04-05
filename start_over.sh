python ailine.py cleanup
python ailine.py init git@github.com:IgorZaton/test-repo.git
cd repo
echo "print('Training...')" > train.py
echo "data" > data.csv
echo "hidden" > .hidden.txt
mkdir .hidden_dir && echo "secret" > .hidden_dir/secret.txt
git add . && git commit -m "Initial" && git push origin main
echo "print('dirty')" >> train.py  # For snapshot
cd ..