/* Batch example:
     vsub batch submit examples/long_job.sas -f class.sas7bdat --wait
   Inputs shipped with -f and outputs to collect live in `jobdir`. */
data jobdir.class_bmi;
  set jobdir.class;
  bmi = weight / height**2 * 703;
run;
