import React from 'react';
import { TextField, InputAdornment } from '@mui/material';

type NumericFieldProps = {
  label: string;
  value?: number | null;
  onChange: (value: number | null) => void;
  placeholder?: string;
  adornment?: string;
  min?: number;
  max?: number;
  step?: number;
};

export default function NumericField(props: NumericFieldProps) {
  const { label, value, onChange, placeholder, adornment, min, max, step } = props;
  return (
    <TextField
      label={label}
      type="number"
      variant="outlined"
      size="small"
      value={value ?? ''}
      onChange={(e) => {
        const v = e.target.value;
        if (v === '') {
          onChange(null);
        } else {
          const parsed = Number(v);
          onChange(Number.isNaN(parsed) ? null : parsed);
        }
      }}
      placeholder={placeholder}
      fullWidth
      inputProps={{ min, max, step }}
      InputProps={{
        endAdornment: adornment ? <InputAdornment position="end">{adornment}</InputAdornment> : undefined
      }}
    />
  );
}

