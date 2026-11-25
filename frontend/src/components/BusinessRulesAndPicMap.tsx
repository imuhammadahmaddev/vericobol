import type { PicMapping } from "@/lib/types";

interface BusinessRulesAndPicMapProps {
  businessRules: string[];
  picMappings: PicMapping[];
}

/** 3270-styled list of extracted business rules and the PIC mapping table. */
export default function BusinessRulesAndPicMap({
  businessRules,
  picMappings,
}: BusinessRulesAndPicMapProps) {
  return (
    <section
      data-testid="business-rules-pic-map"
      aria-label="Business rules and PIC map"
      className="space-y-4 bg-[#0A0E0C] p-3 font-mono text-sm text-[#00FF66]"
      style={{ textShadow: "0 0 4px rgba(0,255,102,0.5)" }}
    >
      <div>
        <h3 className="mb-1 text-xs uppercase tracking-widest">
          Business Rules
        </h3>
        {businessRules.length === 0 ? (
          <p data-testid="business-rules-empty" className="opacity-70">
            No business rules extracted.
          </p>
        ) : (
          <ul data-testid="business-rules-list" className="space-y-1">
            {businessRules.map((rule, i) => (
              <li key={i} className="flex gap-2">
                <span aria-hidden="true">&gt;</span>
                <span>{rule}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h3 className="mb-1 text-xs uppercase tracking-widest">PIC Map</h3>
        {picMappings.length === 0 ? (
          <p data-testid="pic-map-empty" className="opacity-70">
            No PIC mappings.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table
              data-testid="pic-map-table"
              className="w-full border-collapse text-left"
            >
              <thead>
                <tr className="border-b border-[#00FF66]/50 text-xs uppercase">
                  <th scope="col" className="py-1 pr-3">COBOL Field</th>
                  <th scope="col" className="py-1 pr-3">PIC</th>
                  <th scope="col" className="py-1 pr-3">Scale</th>
                  <th scope="col" className="py-1">Rounding</th>
                </tr>
              </thead>
              <tbody>
                {picMappings.map((m, i) => (
                  <tr
                    key={`${m.name}-${i}`}
                    data-testid="pic-map-row"
                    className="border-b border-[#00FF66]/20"
                  >
                    <td className="py-1 pr-3">{m.name}</td>
                    <td className="py-1 pr-3">{m.pic}</td>
                    <td className="py-1 pr-3">{m.scale}</td>
                    <td className="py-1">{m.rounding}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
