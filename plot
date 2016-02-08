#!/bin/bash

OPS=$(echo "${@:1}" | tr -d " ")
FILENAME="scenario${OPS}"

echo "Plotting ${@:1} to $FILENAME"

./simulate "${@:1}" \
  | tee ./${FILENAME}.txt \
  | gnuplot -p -e "set terminal png size 400,300; set yrange [0:110]; plot '-' using 3 with points" \
  > ./${FILENAME}.png

# using 2:1 with points pt 3

# Calculate success frequency
# cat scenario--prop259.txt | cut -d " " -f 3 | xargs -n1 printf "scale=1;%s/1.0\n" | bc -l | sort -n | uniq -c | gnuplot -p -e "set style data boxes; plot '-' using 2:1 with points pt 3"
