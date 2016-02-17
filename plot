#!/bin/bash

OPS=$(echo "${@:1}" | tr -d " ")
FILENAME="scenario${OPS}"
mkdir -p ./out

echo "Plotting ${@:1} to out/$FILENAME"

echo "success total success-rate capacity 1 15 30 exposure-1 exposure-15 exposure-30 guards-till-first-circuit time-till-first-circuit total-time" > ./out/${FILENAME}.txt
./simulate "${@:1}" >> ./out/${FILENAME}.txt

# success rate
gnuplot -p -e "set terminal png size 700,300; set key autotitle columnhead; set key outside; set key bottom; set yrange [0:110]; plot './out/${FILENAME}.txt' using 3 with points" \
  > ./out/success_rate_with_${FILENAME}.png

# exposure
gnuplot -p -e "set terminal png size 700,300; set key autotitle columnhead; set key outside; set key bottom; plot for [col=8:10] './out/${FILENAME}.txt' using col with points ls 1+(col-8)*3" \
  > ./out/exposure_with_${FILENAME}.png

# using 2:1 with points pt 3

# Calculate success frequency
# cat scenario--prop259.txt | cut -d " " -f 3 | xargs -n1 printf "scale=1;%s/1.0\n" | bc -l | sort -n | uniq -c | gnuplot -p -e "set style data boxes; plot '-' using 2:1 with points pt 3"

