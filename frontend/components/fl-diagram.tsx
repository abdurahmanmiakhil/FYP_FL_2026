/** Federated learning (FedAvg) diagram: hospitals train locally, only model weights travel. */
export function FederatedDiagram() {
  const hospital = (x: number, name: string, n: string) => (
    <g transform={`translate(${x},190)`}>
      <rect width="210" height="120" rx="12" className="fill-card stroke-border" strokeWidth="2" />
      <text x="105" y="30" textAnchor="middle" className="fill-foreground text-[15px] font-semibold">
        {name}
      </text>
      <text x="105" y="52" textAnchor="middle" className="fill-muted-foreground text-[12px]">
        {n}
      </text>
      <rect x="20" y="66" width="80" height="38" rx="6" className="fill-muted" />
      <text x="60" y="90" textAnchor="middle" className="fill-foreground text-[11px]">
        slides stay
      </text>
      <rect x="110" y="66" width="80" height="38" rx="6" className="fill-primary/15" />
      <text x="150" y="84" textAnchor="middle" className="fill-foreground text-[11px]">
        local
      </text>
      <text x="150" y="98" textAnchor="middle" className="fill-foreground text-[11px]">
        training
      </text>
    </g>
  );
  return (
    <svg viewBox="0 0 640 330" role="img" aria-labelledby="fl-title fl-desc" className="h-auto w-full">
      <title id="fl-title">Federated averaging between two hospitals</title>
      <desc id="fl-desc">
        Each hospital trains the model on its own slides. Only the model weights are sent to the server, which
        averages them into a global model and sends it back. Slides never leave the hospitals.
      </desc>
      <defs>
        <marker
          id="arrow"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M0 0L10 5L0 10z" className="fill-primary" />
        </marker>
      </defs>
      <g transform="translate(215,20)">
        <rect width="210" height="92" rx="12" className="fill-primary/10 stroke-primary" strokeWidth="2" />
        <text x="105" y="34" textAnchor="middle" className="fill-foreground text-[15px] font-semibold">
          Aggregation server
        </text>
        <text x="105" y="56" textAnchor="middle" className="fill-muted-foreground text-[12px]">
          FedAvg: weighted mean of
        </text>
        <text x="105" y="74" textAnchor="middle" className="fill-muted-foreground text-[12px]">
          the hospitals&apos; weights
        </text>
      </g>
      {hospital(20, "Hospital A - Radboud", "PANDA slides (Netherlands)")}
      {hospital(410, "Hospital B - Karolinska", "PANDA slides (Sweden)")}
      <path
        d="M150 188 C150 150 210 120 240 112"
        className="stroke-primary"
        strokeWidth="2.5"
        fill="none"
        markerEnd="url(#arrow)"
      />
      <path
        d="M270 112 C250 140 200 170 175 188"
        className="stroke-primary"
        strokeWidth="2.5"
        strokeDasharray="6 5"
        fill="none"
        markerEnd="url(#arrow)"
      />
      <path
        d="M490 188 C490 150 430 120 400 112"
        className="stroke-primary"
        strokeWidth="2.5"
        fill="none"
        markerEnd="url(#arrow)"
      />
      <path
        d="M370 112 C390 140 440 170 465 188"
        className="stroke-primary"
        strokeWidth="2.5"
        strokeDasharray="6 5"
        fill="none"
        markerEnd="url(#arrow)"
      />
      <text x="120" y="150" className="fill-muted-foreground text-[11px]">
        weights
      </text>
      <text x="228" y="165" className="fill-muted-foreground text-[11px]">
        global model
      </text>
      <text x="470" y="150" className="fill-muted-foreground text-[11px]">
        weights
      </text>
      <text x="345" y="165" className="fill-muted-foreground text-[11px]">
        global model
      </text>
    </svg>
  );
}
