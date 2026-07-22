import { Accordion, AccordionDetails, AccordionSummary, Typography } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import React from 'react';

export default function Section(props: { title: string; defaultExpanded?: boolean; children: React.ReactNode }) {
  return (
    <Accordion defaultExpanded={props.defaultExpanded} disableGutters sx={{ backgroundColor: 'background.paper', borderRadius: 2, mb: 1, overflow: 'hidden' }}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ borderBottom: '1px solid', borderColor: 'divider', '& .MuiAccordionSummary-content': { my: 0.5 } }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
          {props.title}
        </Typography>
      </AccordionSummary>
      <AccordionDetails sx={{ pt: 2 }}>
        {props.children}
      </AccordionDetails>
    </Accordion>
  );
}

