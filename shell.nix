{ src ? builtins.fetchTarball "https://github.com/NixOS/nixpkgs/archive/nixos-20.09.tar.gz",
  pkgs ? import src {}}:

pkgs.mkShell {
  buildInputs = with pkgs; [
    (python3.withPackages (ps: with ps; [ virtualenv ]))
  ];

  shellHook = ''
  '';
}
