type Props = {
  lede?: string;
};

export function BrandLockup({ lede }: Props) {
  return (
    <div className="brand">
      <img className="brand-mark" src="/mosaicWave-icon.png" width={40} height={40} alt="" />
      <div className="brand-text">
        <h1>mosaicWave</h1>
        {lede ? <p className="lede">{lede}</p> : null}
      </div>
    </div>
  );
}
