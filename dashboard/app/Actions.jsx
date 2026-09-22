"use client";
import ActLoop from "./ActLoop";

// Actions: explain, recommend, then act. Executable act-loop proposals come first (a human
// confirms each write). Advisory cards follow: the causal throttle and the edge right-sizing.
// Every card names the evidence it cites.
export default function Actions({ d }) {
  const adv = d.derived.advisory;
  const loopItems = (d.actions?.proposals?.length || 0) + (d.actions?.active?.length || 0);
  return (
    <>
      {loopItems > 0 && <div className="lbl act-grp">act loop · a human confirms every write</div>}
      <ActLoop actions={d.actions} onChanged={d.fleetChanged} />
      {adv.length > 0 && <div className="lbl act-grp">advisory</div>}
      {adv.map((a) => (
        <div key={`${a.verb}-${a.name}`} className="rec">
          <span className={`actpill ${a.kind}`}>{a.verb}</span>
          <div className="b">
            <div className="nm">{a.name}</div>
            <div className="ds">{a.detail}</div>
            <div className="ct"><b>cites:</b> {a.cites}</div>
          </div>
        </div>
      ))}
      {!loopItems && !adv.length && (
        <div className="empty">{d.recs?.source === "unavailable" ? "Prometheus unavailable" : "no actions · every workload is right-sized"}</div>
      )}
    </>
  );
}
