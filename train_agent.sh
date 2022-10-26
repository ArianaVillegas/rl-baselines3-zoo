for i in {1..10}
do
   echo "Agent $i"
   python train.py --algo a2c --env $1 --num-threads 12 --seed $i -tb tboard --save-freq $2
done
