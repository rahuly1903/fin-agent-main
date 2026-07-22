import React from 'react';
import { Grid } from '@mui/material';
import NumericField from './NumericField';
import { NumericRange } from '@/types/filters';

type MinMaxFieldProps = {
  labelMin?: string;
  labelMax?: string;
  range: NumericRange;
  onChange: (range: NumericRange) => void;
  adornment?: string;
  step?: number;
};

export default function MinMaxField(props: MinMaxFieldProps) {
  const { labelMin = 'Min', labelMax = 'Max', range, onChange, adornment, step } = props;
  return (
    <Grid container spacing={1.5}>
      <Grid item xs={6}>
        <NumericField label={labelMin} value={range.min ?? null} onChange={(v) => onChange({ ...range, min: v })} adornment={adornment} step={step} />
      </Grid>
      <Grid item xs={6}>
        <NumericField label={labelMax} value={range.max ?? null} onChange={(v) => onChange({ ...range, max: v })} adornment={adornment} step={step} />
      </Grid>
    </Grid>
  );
}

