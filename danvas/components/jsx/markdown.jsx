
function Component({{ props }}) {{
  const _th = props._th || {{}};
  return (
    <>
      <style>{{`{css}`}}</style>
      <div className="pc-md" style={{_th}}
           dangerouslySetInnerHTML={{{{ __html: props.html || "" }}}} />
    </>
  );
}}
