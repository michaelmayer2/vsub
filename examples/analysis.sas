/* Interactive example:
     vsub run examples/analysis.sas --up class.sas7bdat --down vsub.class_bmi -o out/
   `vsub` = remote_root on Viya (persistent if HOME is mounted). */
data vsub.class_bmi;
  set vsub.class;
  bmi = weight / height**2 * 703;
run;

proc means data=vsub.class_bmi n mean min max;
  var bmi;
run;
